"""
llm.py -- stage S5: AI interpretation (Anthropic API), batched per chapter.

One call per chapter: chapter context (title, summaries, change list) goes in, a
schema-bound JSON interpretation comes out. Without a valid key the same prompts are
exported as files (chat workflow) and the pipeline finishes deterministically with the
AI fields left empty. Answers are cached by hash over (model, prompt).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from pathlib import Path

from ..text.textnorm import n2, split_sentences

SEMANTIC_LABELS = ["equivalent", "clarified", "extended", "restricted",
                   "new_obligation", "removed_obligation", "moved", "optional",
                   "informative", "contradictory"]

# Human-readable rendering of the schema enums, per output language. The JSON stays in
# neutral English; only the rendered documents are localized.
LABELS = {
    "de": {
        "equivalent": "gleichbedeutend", "clarified": "präzisiert", "extended": "erweitert",
        "restricted": "eingeschränkt", "new_obligation": "neue Pflicht",
        "removed_obligation": "entfallene Pflicht", "moved": "verschoben",
        "optional": "optional", "informative": "informativ",
        "contradictory": "widersprüchlich",
        "tightened": "verschärft", "relaxed": "gelockert", "unchanged": "unverändert",
        "high": "hoch", "medium": "mittel", "low": "gering",
        "changed": "geändert", "new": "neu", "removed": "entfallen",
    },
}

LANGUAGE_NAMES = {"de": "German", "en": "English", "fr": "French", "es": "Spanish",
                  "it": "Italian", "nl": "Dutch"}

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a domain expert analysing two editions of a technical standard{title}. You are "
    "preparing training material on what changed between the old and the new edition. Work "
    "precisely and soberly; never speculate. Strictly distinguish editorial rewording "
    "(equivalent) from a substantive change. The deterministic findings supplied with each "
    "change (parameter-value diff, modality shift, reference diff) are computed mechanically: "
    "use them, but if a finding contradicts the text, set contradiction_flag=true and briefly "
    "explain why. Pay close attention to CROSS-REFERENCES: for changed internal references "
    "(sections, annexes, tables, figures) distinguish mere renumbering caused by the new "
    "chapter structure from a genuinely NEW reference target (then fill cross_reference_note); "
    "changed references to other standards are almost always substantive. For `evidence`, "
    "quote a short passage VERBATIM from the old or the new text: the literal span only, no "
    "paraphrase, no added labels or prefixes. Also report in pipeline_feedback anything that "
    "looks like a PREPROCESSING artefact rather than a real change: stray page numbers, torn "
    "or merged paragraphs, swallowed hyphens, formula or encoding residue, obviously wrong "
    "alignments, or gaps in the source document. "
    "Write every free-text value in {language}; keep the enum values exactly as given in the "
    "schema (English). Answer with the required JSON only, no markdown fences."
)


def build_system_prompt(language: str = "de", title: str = "") -> str:
    """System prompt for the interpretation: neutral and English, with the free-text output
    language taken from the configuration (the compared standard stays German by default)."""
    lang = LANGUAGE_NAMES.get((language or "de").lower(), language or "German")
    return _SYSTEM_PROMPT_TEMPLATE.format(
        title=f" ({title})" if title else "", language=lang)


CHAPTER_SCHEMA_DOC = """{
 "section_id": "<id>",
 "summary_old": "2-4 sentences: what this chapter says in the OLD edition ('' if the chapter is new)",
 "summary_new": "2-4 sentences: what this chapter says in the NEW edition ('' if it was removed)",
 "change_overview": "1-3 sentences: what changes in this chapter overall",
 "training_relevance": "high|medium|low",
 "keywords": ["1-4 keywords from the supplied list, most relevant first"],
 "practical_note": "1-2 sentences: a concrete practical example that highlights the change(s) ('' if none)",
 "pipeline_feedback": [
   {"phase": "ingest_pdf|ingest_docx|alignment|diff|source",
    "finding": "observed preprocessing artefact (e.g. stray page number, swallowed hyphen, formula residue, wrongly split paragraph, gap in the source document)",
    "change_indices": [<int>]}
 ],
 "interpretations": [
   {"change_index": <int, index from the change list>,
    "semantic_label": "equivalent|clarified|extended|restricted|new_obligation|removed_obligation|moved|optional|informative|contradictory",
    "obligation": "tightened|relaxed|unchanged",
    "change": "1-2 sentences describing the substance of the change",
    "impact": "1 sentence on the practical impact ('' if none)",
    "cross_reference_note": "meaning of changed internal/external references: 'renumbered' for a pure structural follow-on, otherwise a short explanation of the new target ('' if no reference change)",
    "evidence": "verbatim short quote from the old or the new text",
    "confidence": "high|medium|low",
    "contradiction_flag": false}
 ]
}"""

# additional schema, only appended when the chapter contains tables/figures
ASSET_SCHEMA_DOC = """,
 "tables": [
   {"table": "<short caption/name of the table>",
    "status": "changed|new|removed",
    "change": "1-2 sentences: which rows/values/limits change (be concrete)",
    "value_changes": ["concrete value change(s) old->new, e.g. 'droop: 5 % -> 4 %'; [] if none"],
    "impact": "1 sentence on the practical impact ('' if none)",
    "evidence": "verbatim short quote from a table cell or caption (old or new)",
    "confidence": "high|medium|low"}
 ],
 "figures": [
   {"figure": "<short caption/name of the figure>",
    "status": "changed|new|removed",
    "change": "1-2 sentences: what changes in the figure's content/message",
    "impact": "1 sentence on the practical impact ('' if none)",
    "evidence": "verbatim short quote from the caption (old or new)",
    "confidence": "high|medium|low"}
 ]
}"""


def label(value: str, language: str = "de") -> str:
    """Render a schema enum value in the output language (identity for English)."""
    return LABELS.get((language or "de").lower(), {}).get(value, value or "")


# ------------------------------------------------------------------ Key & Client
# Provider -> (default env var for the key, default base URL). All except
# "anthropic" are addressed via the OpenAI-compatible /chat/completions API
# (works for OpenAI, Azure, the Google Gemini OpenAI endpoint, Mistral, Groq,
# Together, OpenRouter, Ollama and many more).
PROVIDERS = {
    "anthropic":         ("ANTHROPIC_API_KEY",  "https://api.anthropic.com"),
    "openai":            ("OPENAI_API_KEY",      "https://api.openai.com/v1"),
    "azure_openai":      ("AZURE_OPENAI_API_KEY", None),   # base_url mandatory from config
    "google":            ("GOOGLE_API_KEY",      "https://generativelanguage.googleapis.com/v1beta/openai"),
    "mistral":           ("MISTRAL_API_KEY",     "https://api.mistral.ai/v1"),
    "groq":              ("GROQ_API_KEY",         "https://api.groq.com/openai/v1"),
    "together":          ("TOGETHER_API_KEY",     "https://api.together.xyz/v1"),
    "openrouter":        ("OPENROUTER_API_KEY",   "https://openrouter.ai/api/v1"),
    "ollama":            ("OLLAMA_API_KEY",        "http://localhost:11434/v1"),
    "openai_compatible": ("LLM_API_KEY",           None),   # generic, base_url from config
}


def resolve_key(root: Path, provider: str = "anthropic", key_env: str | None = None) -> str | None:
    """Key lookup: explicit env name -> provider default env -> generic LLM_API_KEY
    -> file .api_key. Ollama may run without a key (local server)."""
    default_env = PROVIDERS.get(provider, (None, None))[0]
    for var in (key_env, default_env, "LLM_API_KEY"):
        if var and os.environ.get(var):
            return os.environ[var].strip()
    for cand in (root / ".api_key", root.parent / "Pipeline" / ".api_key"):
        if cand.exists():
            return cand.read_text().strip()
    if provider == "ollama":
        return "ollama"       # local server, key often irrelevant
    return None


class LlmClient:
    def __init__(self, root: Path, model: str, cache_dir: Path, export_dir: Path,
                 provider: str = "anthropic", base_url: str | None = None,
                 key_env: str | None = None, api_version: str | None = None,
                 max_retries: int = 3, system: str | None = None):
        self.model = model
        self.system = system or build_system_prompt()
        self.provider = (provider or "anthropic").lower()
        self.cache_dir = cache_dir
        self.export_dir = export_dir
        self.max_retries = max_retries
        self.api_version = api_version         # Azure only
        cache_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url or PROVIDERS.get(self.provider, (None, None))[1]
        self.key = resolve_key(root, self.provider, key_env)
        self._anth = None
        self.live = False
        if not self.key:
            print(f"  [llm] Kein Key für Provider '{self.provider}' — Prompt-Export-Modus.")
            return
        if self.provider == "anthropic":
            try:
                import anthropic
                self._anth = anthropic.Anthropic(api_key=self.key)
                self.live = True
            except Exception as e:
                print(f"  [llm] anthropic-SDK nicht nutzbar ({type(e).__name__}: {e}) — Export-Modus.")
                return
        else:
            if not self.base_url:
                print(f"  [llm] Provider '{self.provider}': base_url fehlt (config: llm_base_url) — Export-Modus.")
                return
            self.live = True

    def _cache_key(self, user: str) -> Path:
        # IMPORTANT: the cache key is deliberately built only from model + system prompt +
    # user prompt (NOT the provider), so already-injected interpretations are
    # provider-independent.
        h = hashlib.sha1((self.model + "\x00" + self.system + "\x00" + user).encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    # ---- provider calls -------------------------------------------------
    def _complete_anthropic(self, user: str, max_tokens: int) -> str:
        # NOTE: no `temperature` here -- newer Anthropic models (e.g. claude-sonnet-5)
        # reject it as deprecated (HTTP 400). Determinism is not guaranteed at the API level
        # anyway; the response cache keeps re-runs stable.
        # Disable extended thinking: newer models (e.g. claude-sonnet-5) think by default,
        # which for a structured JSON-extraction task only burns the token budget (the whole
        # `max_tokens` can be spent on a thinking block, leaving no answer text). We want the
        # answer directly. Older/cheaper models may not accept the `thinking` parameter, so
        # we fall back to a plain call if it is rejected.
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=self.system,
                      messages=[{"role": "user", "content": user}])
        try:
            m = self._anth.messages.create(thinking={"type": "disabled"}, **kwargs)
        except Exception as e:
            if "thinking" in str(e).lower():
                m = self._anth.messages.create(**kwargs)
            else:
                raise
        # collect all text blocks (a model may still prepend non-text blocks, so indexing
        # content[0] blindly can raise AttributeError); join their text robustly
        parts = [b.text for b in m.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()

    def _complete_openai_compatible(self, user: str, max_tokens: int) -> str:
        import urllib.request
        url = self.base_url.rstrip("/") + "/chat/completions"
        if self.provider == "azure_openai":
            # Azure: base_url is the deployment path; api-version as a query param
            ver = self.api_version or "2024-06-01"
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-version={ver}"
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.provider == "azure_openai":
            headers["api-key"] = self.key
        else:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp["choices"][0]["message"]["content"].strip()

    def _complete(self, user: str, max_tokens: int) -> str:
        if self.provider == "anthropic":
            return self._complete_anthropic(user, max_tokens)
        return self._complete_openai_compatible(user, max_tokens)

    def _parse_json_answer(self, txt: str, user: str, tag: str) -> dict | None:
        """Strip code fences, parse JSON, cache on success; export the raw text on failure."""
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", (txt or "").strip())
        try:
            data = json.loads(txt)
        except json.JSONDecodeError:
            (self.export_dir / f"{tag}.FAILED.txt").write_text(txt, encoding="utf-8")
            return None
        self._cache_key(user).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def ask_json_batch(self, items: list[tuple[str, str]],
                       max_tokens: int = 16000, poll_s: int = 15) -> dict[str, dict | None]:
        """Resolve many prompts at once via the Anthropic Message Batches API (~50% cheaper).

        Cached prompts are served from the cache; only the remainder is submitted. Batches are
        asynchronous, so this polls until the batch has ended. Falls back to sequential calls
        for non-Anthropic providers or in export mode.

        Args:
            items: ``(tag, prompt)`` pairs, one per chapter.
        Returns:
            Mapping ``tag -> parsed JSON`` (``None`` where the answer was unusable).
        """
        out: dict[str, dict | None] = {}
        todo: list[tuple[str, str]] = []
        for tag, user in items:
            cp = self._cache_key(user)
            if cp.exists():
                out[tag] = json.loads(cp.read_text(encoding="utf-8"))
            else:
                todo.append((tag, user))
        if not todo:
            return out
        if not self.live or self.provider != "anthropic":
            for tag, user in todo:            # export mode / other providers: sequential
                out[tag] = self.ask_json(user, tag, max_tokens)
            return out

        # index-based custom_ids: the API only allows [a-zA-Z0-9_-]
        by_cid = {f"c{i}": (tag, user) for i, (tag, user) in enumerate(todo)}
        requests = [{"custom_id": cid,
                     "params": {"model": self.model, "max_tokens": max_tokens,
                                "system": self.system, "thinking": {"type": "disabled"},
                                "messages": [{"role": "user", "content": user}]}}
                    for cid, (_, user) in by_cid.items()]
        batch = self._anth.messages.batches.create(requests=requests)
        print(f"  [llm] Batch {batch.id}: {len(requests)} Kapitel eingereicht (~50 % günstiger). "
              f"Warte auf Verarbeitung …")
        while True:
            b = self._anth.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(poll_s)
        for r in self._anth.messages.batches.results(batch.id):
            tag, user = by_cid.get(r.custom_id, (None, None))
            if tag is None:
                continue
            if getattr(r.result, "type", None) != "succeeded":
                err = getattr(getattr(r.result, "error", None), "type", r.result.type)
                print(f"  [llm] Batch {tag}: {err} — extraktiver Fallback.")
                out[tag] = None
                continue
            txt = "".join(bl.text for bl in r.result.message.content
                          if getattr(bl, "type", None) == "text")
            out[tag] = self._parse_json_answer(txt, user, tag)
        for tag, _ in todo:                   # anything the API never returned
            out.setdefault(tag, None)
        return out

    def ask_json(self, user: str, tag: str, max_tokens: int = 16000) -> dict | None:
        cp = self._cache_key(user)
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))
        if not self.live:
            (self.export_dir / f"{tag}.txt").write_text(
                "SYSTEM:\n" + self.system + "\n\nUSER:\n" + user, encoding="utf-8")
            return None
        txt = ""
        for attempt in range(self.max_retries):
            try:
                txt = self._complete(user, max_tokens)
                txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
                data = json.loads(txt)
                cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except json.JSONDecodeError:
                if attempt == self.max_retries - 1:
                    (self.export_dir / f"{tag}.FAILED.txt").write_text(txt, encoding="utf-8")
                    return None
            except Exception as e:
                wait = 2 ** attempt * 5
                print(f"  [llm] {tag} [{self.provider}]: {type(e).__name__}, retry in {wait}s")
                time.sleep(wait)
        return None


# ------------------------------------------------------------------ prompt building
def _clip(t: str | None, n: int) -> str:
    t = (t or "").strip()
    return t if len(t) <= n else t[:n] + " …"


def _change_block(i: int, c: dict) -> str:
    L = [f"[{i}] TYP={c['kind']}"]
    if c.get("modality"):
        mod = c["modality"]
        if "shift" in mod:
            L.append(f"    Modalität: {mod.get('old')} → {mod.get('new')} ({mod['shift']})")
        elif mod.get("new"):
            L.append(f"    Modalität (neu): {mod['new']}")
        elif mod.get("old"):
            L.append(f"    Modalität (alt): {mod['old']}")
    kw = c.get("kennwerte") or {}
    parts = [f"{ch['old']['raw']} → {ch['new']['raw']}" for ch in kw.get("changed", [])]
    parts += [f"+{a['raw']}" for a in kw.get("added", [])[:6]]
    parts += [f"−{r['raw']}" for r in kw.get("removed", [])[:6]]
    if parts:
        L.append(f"    Kennwerte: {'; '.join(parts)}")
    refs = c.get("refs") or {}
    ri = [f"+{r}" for r in refs.get("internal_added", [])] + \
         [f"−{r}" for r in refs.get("internal_removed", [])]
    if ri:
        L.append(f"    Querverweise (intern): {'; '.join(ri[:10])}")
    rparts = [f"+{r}" for r in refs.get("external_added", [])] + \
             [f"−{r}" for r in refs.get("external_removed", [])]
    if rparts:
        L.append(f"    Referenzen: {'; '.join(rparts[:8])}")
    if c.get("moved_from"):
        L.append(f"    (hierher verschoben aus {c['moved_from']})")
    if c.get("moved_to"):
        L.append(f"    (verschoben nach {c['moved_to']})")
    if c.get("old_text"):
        L.append(f"    ALT: {_clip(c['old_text'], 900)}")
    if c.get("new_text"):
        L.append(f"    NEU: {_clip(c['new_text'], 900)}")
    return "\n".join(L)


def _tabmap(secs: dict) -> dict:
    """id -> table object across all sections (secs: {section_id: section})."""
    m = {}
    for s in secs.values():
        for t in s.get("tables") or []:
            m[t["id"]] = t
    return m


def _render_cells(t: dict, max_rows: int = 40, max_chars: int = 1100) -> str:
    rows = t.get("cells") or []
    buf = [" | ".join((c or "").strip() for c in r) for r in rows[:max_rows]]
    body = " ‖ ".join(buf)
    if len(rows) > max_rows:
        body += f" … (+{len(rows) - max_rows} Zeilen)"
    return _clip(body, max_chars)


def _figs(secs: dict, ids) -> list[dict]:
    out = []
    for sid in ids or []:
        s = secs.get(sid)
        if s:
            out += s.get("figures") or []
    return out


def chapter_assets_block(ch: dict, o_secs: dict, n_secs: dict, max_chars: int = 6500) -> str:
    """Text extract of the chapter tables (with cell contents) and figure captions,
    paired via ch['tables_diff']. Empty string if the chapter has no assets."""
    om, nm = _tabmap(o_secs), _tabmap(n_secs)
    tlines = []
    for td in ch.get("tables_diff") or []:
        cap = (td.get("caption") or "").strip() or "(ohne Caption)"
        kind = td.get("kind")
        if kind == "matched":
            if td.get("identical"):
                continue
            ot, nt = om.get(td.get("old")), nm.get(td.get("new"))
            tlines.append(f"  [GEÄNDERT] {cap}  (geänderte Zeilen: {td.get('rows_changed', '?')})")
            if ot:
                tlines.append("    ALT: " + _render_cells(ot))
            if nt:
                tlines.append("    NEU: " + _render_cells(nt))
        elif kind == "new":
            nt = nm.get(td.get("new"))
            tlines.append(f"  [NEU] {cap}")
            if nt:
                tlines.append("    NEU: " + _render_cells(nt))
        elif kind == "removed":
            ot = om.get(td.get("old"))
            tlines.append(f"  [ENTFALLEN] {cap}")
            if ot:
                tlines.append("    ALT: " + _render_cells(ot))
    fo, fn = _figs(o_secs, ch.get("old_ids")), _figs(n_secs, ch.get("new_ids"))
    flines = []
    if fo:
        flines.append("  BILDER ALT: " + " ‖ ".join(_clip(f.get("caption") or "", 160) for f in fo))
    if fn:
        flines.append("  BILDER NEU: " + " ‖ ".join(_clip(f.get("caption") or "", 160) for f in fn))
    if not tlines and not flines:
        return ""
    block = ["", "TABELLEN & BILDER (Struktur-Extrakt — enthält Kennwerte/Grenzwerte; "
             "für die Deutung berücksichtigen):"]
    if tlines:
        block += [" TABELLEN:"] + tlines
    if flines:
        block += [" BILDER:"] + flines
    s = "\n".join(block)
    return s if len(s) <= max_chars else s[:max_chars] + "\n  … (gekürzt)"


def asset_haystack(ch: dict, o_secs: dict, n_secs: dict) -> str:
    """Cell/caption text of all chapter tables and figures (for the evidence guard)."""
    om, nm = _tabmap(o_secs), _tabmap(n_secs)
    parts = []
    for td in ch.get("tables_diff") or []:
        for m, k in ((om, "old"), (nm, "new")):
            t = m.get(td.get(k))
            if t:
                parts.append(t.get("caption") or "")
                for r in t.get("cells") or []:
                    parts.append(" ".join(r))
    for f in _figs(o_secs, ch.get("old_ids")) + _figs(n_secs, ch.get("new_ids")):
        parts.append(f.get("caption") or "")
    return n2(" ".join(parts)).lower()


def check_asset_evidence(item: dict, hay: str) -> dict:
    """Evidence guard for table/figure interpretations, see :func:`check_evidence`."""
    ev = n2(item.get("evidence") or "").lower()
    return {"evidence_ok": _asset_evidence_ok(ev, hay), **evidence_metrics(ev, hay)}


def _asset_evidence_ok(ev: str, hay: str) -> bool:
    """The historical asset rule -- kept unchanged (40-character probe)."""
    if len(ev) < 8:
        return False
    return ev in hay or ev[:40] in hay


def build_chapter_prompt(ch: dict, old_excerpt: str, new_excerpt: str,
                         o_secs: dict | None = None, n_secs: dict | None = None,
                         max_changes: int = 40) -> tuple[str, list[int]]:
    """Prompt + list of included change indices (priority: value/modality/new)."""
    idx = list(range(len(ch["changes"])))

    def prio(i):
        c = ch["changes"][i]
        p = 0.0
        if c.get("kennwerte", {}).get("changed"):
            p += 4
        if (c.get("modality") or {}).get("shift") in ("verschaerft", "gelockert"):
            p += 3
        if c["kind"] in ("new", "removed") and (c.get("modality", {}).get("new") in ("muss", "darf_nicht")
                                                or c.get("modality", {}).get("old") in ("muss", "darf_nicht")):
            p += 3
        if c["kind"] in ("moved_in", "moved_away"):
            p += 1
        p += min(len(c.get("old_text") or "") + len(c.get("new_text") or ""), 2000) / 2000
        if c.get("semantic_equal"):
            p -= 5
        return -p
    idx.sort(key=prio)
    sel = sorted(idx[:max_changes])

    cid = ch.get("new_id") or ch.get("old_id")
    head = [f"KAPITEL {cid} — {ch['title']}  (Teil: {ch['part']}"
            + (", NORMATIV/INFORMATIV-STATUS GEÄNDERT!" if ch.get("part_changed") else "") + ")",
            f"Kapitel-Modus: {ch['mode']}; nicht geänderte Absätze: {ch.get('n_identical', 0)}",
            "", "AUSZUG ALTE FASSUNG:", _clip(old_excerpt, 1600) or "(Kapitel existiert in der alten Fassung nicht)",
            "", "AUSZUG NEUE FASSUNG:", _clip(new_excerpt, 1600) or "(Kapitel in der neuen Fassung entfallen)",
            "", f"ÄNDERUNGEN ({len(sel)} von {len(ch['changes'])}, deterministisch ermittelt):"]
    for i in sel:
        head.append(_change_block(i, ch["changes"][i]))
    assets = chapter_assets_block(ch, o_secs, n_secs) if (o_secs is not None and n_secs is not None) else ""
    if assets:
        head.append(assets)
    from .keywords import TAXONOMY
    if assets:
        schema = CHAPTER_SCHEMA_DOC.rstrip()[:-1].rstrip() + ASSET_SCHEMA_DOC
        task = ("AUFGABE: Erzeuge das folgende JSON (deutungen für ALLE oben gelisteten Indizes; "
                "zusätzlich die Tabellen und Bilder oben in 'tables'/'figures' deuten):")
    else:
        schema = CHAPTER_SCHEMA_DOC
        task = "AUFGABE: Erzeuge das folgende JSON (deutungen für ALLE oben gelisteten Indizes):"
    head += ["", "VERFÜGBARE SCHLAGWORTE (wähle 1-4 passende): " + " | ".join(TAXONOMY.keys()),
             "", task, schema]
    return "\n".join(head), sel


# ------------------------------------------------------------------ Fallback & Guard
def extractive_summary(text: str, n: int = 3) -> str:
    sents = [s for s in split_sentences(text) if len(s) > 40]
    return " ".join(sents[:n])


# Ellipsis marks a model writes into a supposedly verbatim quote: U+2026 and three
# dots, each also in brackets, with any surrounding whitespace.
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")


def evidence_fragments(ev: str) -> list[str]:
    """Split a quote at ellipsis marks; fragments empty after trimming are dropped."""
    return [f for f in (part.strip() for part in _ELLIPSIS.split(ev)) if f]


def _matched_chars(fragment: str, hay: str) -> int:
    """Length of the longest prefix of ``fragment`` that occurs verbatim in ``hay``.

    Binary search: prefix containment is monotone -- if ``fragment[:n]`` is present,
    every shorter prefix is present too.
    """
    lo, hi = 0, len(fragment)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fragment[:mid] in hay:
            lo = mid
        else:
            hi = mid - 1
    return lo


def evidence_metrics(ev: str, hay: str) -> dict:
    """The three diagnostic quantities next to ``evidence_ok`` (ENT-18, ENT-19).

    ``evidence_strict``
        every fragment of the quote occurs completely in ``hay``.
    ``evidence_match_chars``
        characters actually covered, summed over the fragments -- five fragments of
        eight characters are worth less than one contiguous span of forty.
    ``evidence_fragments``
        number of fragments after splitting at the ellipsis marks.

    The split affects these three values only; ``evidence_ok`` keeps its historical
    definition so that the baseline stays comparable.
    """
    frags = evidence_fragments(ev)
    matched = [_matched_chars(f, hay) for f in frags]
    return {
        "evidence_strict": bool(frags) and all(m == len(f) for m, f in zip(matched, frags)),
        "evidence_match_chars": sum(matched),
        "evidence_fragments": len(frags),
    }


def change_haystack(deutung: dict, ch: dict) -> str:
    """Old and new text of the addressed change record, normalized and lower case.

    An absent or out-of-range ``change_index`` yields an empty haystack, so the
    interpretation fails closed instead of being checked against a foreign record.
    """
    i = deutung.get("change_index")
    texts = []
    if isinstance(i, int) and 0 <= i < len(ch["changes"]):
        c = ch["changes"][i]
        texts = [c.get("old_text") or "", c.get("new_text") or ""]
    return " ".join(n2(t).lower() for t in texts)


def check_evidence(deutung: dict, ch: dict) -> dict:
    """Check an interpretation's quote against its change record.

    Returns the four fields written into ``deutung.json``: the unchanged
    ``evidence_ok`` plus the diagnostics of :func:`evidence_metrics`.
    """
    ev = n2(deutung.get("evidence") or "").lower()
    hay = change_haystack(deutung, ch)
    return {"evidence_ok": _evidence_ok(ev, hay), **evidence_metrics(ev, hay)}


def _evidence_ok(ev: str, hay: str) -> bool:
    """The historical rule, unchanged: 15-character probe plus short-quote branch.

    Deliberately blind to ellipsis marks -- it only ever sees the fragment before the
    first one. That weakness is what ``evidence_strict`` measures; changing this
    function would break parity with the baseline.
    """
    if not ev:
        return False
    if len(ev) < 15:
        # short quote only ok if it fully covers the (short) paragraph
        return bool(hay.strip()) and ev == hay.strip()
    probe = ev[:60]
    return probe[:15] in hay or ev in hay


def run_deutung(synopse: dict, old_doc: dict, new_doc: dict, root: Path, out_dir: Path,
                model: str, scope: str = "core", provider: str = "anthropic",
                base_url: str | None = None, key_env: str | None = None,
                api_version: str | None = None, batch: bool = False,
                language: str = "de", title: str = "") -> dict:
    """Per-chapter interpretation. scope=core: chapters with substantive changes.
    provider/base_url/key_env/api_version steer the LLM backend; without a key the
    prompts are exported and summaries fall back to extractive.
    batch=True submits all uncached chapters in one Anthropic message batch (~50% cheaper,
    asynchronous). The JSON schema is neutral English; `language` decides in which language
    the free-text values are written, `title` names the compared standard in the prompt."""
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    client = LlmClient(root, model, out_dir / "llm_cache", out_dir / "llm_prompts",
                       provider=provider, base_url=base_url, key_env=key_env,
                       api_version=api_version,
                       system=build_system_prompt(language, title))

    def sec_excerpt(secs, ids):
        txt = []
        for sid in ids:
            s = secs.get(sid)
            if s:
                txt.append(" ".join(p.get("n1", p.get("n0", "")) for p in s["paragraphs"]))
        return " ".join(txt)

    # ---- phase 1: build one prompt per in-scope chapter --------------------------
    jobs = []          # (ch, cid, tag, prompt, sel, old_x, new_x)
    for ch in synopse["chapters"]:
        substantive = [c for c in ch["changes"] if not c.get("semantic_equal")]
        if scope == "core" and not substantive and not ch.get("part_changed"):
            continue
        old_x = sec_excerpt(o_secs, ch.get("old_ids") or [])
        new_x = sec_excerpt(n_secs, ch.get("new_ids") or [])
        prompt, sel = build_chapter_prompt(ch, old_x, new_x, o_secs, n_secs)
        cid = ch.get("new_id") or ch.get("old_id")
        jobs.append((ch, cid, re.sub(r"[^\w.]", "_", str(cid)), prompt, sel, old_x, new_x))

    # ---- phase 2: resolve the answers --------------------------------------------
    # batch mode submits every uncached chapter in one asynchronous batch (~50% cheaper);
    # otherwise the chapters are asked one by one.
    if batch:
        answers = client.ask_json_batch([(j[2], j[3]) for j in jobs])
    else:
        answers = {j[2]: client.ask_json(j[3], tag=j[2]) for j in jobs}

    # ---- phase 3: assemble, guard the evidence, collect the review queue ----------
    results = []
    review = []
    n_llm = 0
    for ch, cid, tag, prompt, sel, old_x, new_x in jobs:
        data = answers.get(tag)
        if data is None:
            # fallback: extractive
            data = {"section_id": cid,
                    "summary_old": extractive_summary(old_x),
                    "summary_new": extractive_summary(new_x),
                    "change_overview": "",
                    "training_relevance": None, "practical_note": "",
                    "interpretations": [], "_source": "extractive"}
        else:
            n_llm += 1
            data["_source"] = "llm"
            for d in data.get("interpretations", []):
                d.update(check_evidence(d, ch))
                # the review queue still keys on evidence_ok (ENT-18): it switches to
                # evidence_strict once the difference between both is quantified
                if d.get("contradiction_flag") or not d["evidence_ok"]:
                    review.append({"section_id": cid, **d})
            # table/figure interpretations: check evidence against cells/captions
            if data.get("tables") or data.get("figures"):
                hay = asset_haystack(ch, o_secs, n_secs)
                for a in (data.get("tables") or []):
                    a.update(check_asset_evidence(a, hay))
                    if not a["evidence_ok"]:
                        review.append({"section_id": cid, "asset": "table", **a})
                for a in (data.get("figures") or []):
                    a.update(check_asset_evidence(a, hay))
                    if not a["evidence_ok"]:
                        review.append({"section_id": cid, "asset": "figure", **a})
        data["_changes_total"] = len(ch["changes"])
        data["_changes_interpreted"] = sel if data.get("_source") == "llm" else []
        results.append(data)

    # aggregate preprocessing feedback -> feed it back to the pipeline phases
    feedback = []
    for r in results:
        for fb in r.get("pipeline_feedback") or []:
            feedback.append({"section_id": r.get("section_id"), **fb})
    out = {"model": model if client.live else None, "mode": "api" if client.live else "export",
           "language": language,
           "n_chapters": len(results), "n_llm": n_llm, "chapters": results,
           "review_queue": review, "pipeline_feedback": feedback}
    (out_dir / "deutung.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    if feedback:
        L = ["# Vorverarbeitungs-Feedback aus der KI-Deutung\n",
             "_Von der Deutung gemeldete Artefakte — Kandidaten für Verbesserungen "
             "in Ingest/Alignment/Diff (Phase in Klammern)._\n"]
        from collections import Counter
        by_phase = Counter(fb["phase"] for fb in feedback)
        L.append("**Verteilung:** " + ", ".join(f"{p}: {n}" for p, n in by_phase.most_common()) + "\n")
        for fb in feedback:
            idx = f" (Änderungen {fb['change_indices']})" if fb.get("change_indices") else ""
            L.append(f"- **{fb['section_id']}** [{fb['phase']}]{idx}: {fb['finding']}")
        (out_dir / "pipeline_feedback.md").write_text("\n".join(L), encoding="utf-8")
    return out
