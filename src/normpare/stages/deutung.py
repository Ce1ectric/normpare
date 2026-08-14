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
from collections import Counter
from pathlib import Path
from typing import Protocol, runtime_checkable

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


# ------------------------------------------------------------------ four-axis taxonomy
# ENT-01. One flat label cannot carry three statements at once, and ``semantic_label``
# tries to. Measured on ``out/4110_2026-08b`` (1749 interpretations), ``restricted``
# splits 14 / 15 / 13 over ``tightened`` / ``relaxed`` / ``unchanged``: the value means
# "the scope was restricted" (fewer cases covered -- a relaxation for whoever is bound)
# and "the requirement was restricted" (a tightening) at the same time. Another 84
# interpretations claim a duty appeared or fell away and report ``unchanged`` with it.
# Together roughly 7 % contradict themselves, not for want of model quality but because
# the schema does not keep the statements apart.
#
# Four axes keep them apart. They are introduced *additively*: ``semantic_label`` and
# ``obligation`` keep running unchanged, so both readings are collected on the same
# chapters and the replacement can be decided on measurements rather than on intent.

#: Axis A -- the structural operation, keyed by the ``kind`` of the change record.
#: **Pipeline-owned** (ENT-51): deterministic, never asked of the model. ``cosmetic``
#: is a modification like any other here; that its wording is semantically equal is a
#: statement about axis B, and mixing it in is the very defect this taxonomy removes.
#: ``identical`` never reaches a change record (the diff counts it as ``n_identical``);
#: it is listed because the paragraph aligner emits it and a future caller may pass it.
STRUCTURAL_OPERATIONS = {
    "new": "added",
    "removed": "removed",
    "changed": "modified",
    "similar": "modified",
    "cosmetic": "modified",
    "merged": "merged",
    "split": "split",
    "moved_in": "moved",
    "moved_away": "moved",
    "identical": "unchanged",
}

#: Axis B -- what happens to the *statement*, without judging its effect. ``narrowed``
#: replaces ``restricted`` and is a statement about SCOPE only; whoever means strictness
#: uses axis C.
SEMANTIC_STATUS = ["equivalent", "clarified", "extended", "narrowed", "replaced",
                   "contradictory", "indeterminate"]

#: Axis C -- the direction of the duty. ``not_applicable`` is for non-normative text,
#: which today is forced into ``unchanged`` and mixed with genuine non-changes (168 of
#: them in the 4110 run).
NORMATIVE_DIRECTIONS = ["tightened", "relaxed", "unchanged", "not_applicable",
                        "indeterminate"]

#: Axis D -- which components of the standard a change touches. Multi-valued, most
#: important first (the ``keywords`` convention). The vocabulary is a proposal and not
#: yet confirmed domain knowledge, which is what :data:`OTHER_COMPONENT` is for.
#: Four of the ten values are told apart by their description rather than by the subject
#: matter (AP-14, F-2); the separating rules therefore live in the field description in
#: :data:`CHAPTER_SCHEMA_DOC`, where the model reads them, not in a comment.
AFFECTED_COMPONENTS = ["proof_obligation", "limit_value", "procedure", "deadline",
                       "responsibility", "documentation", "scope", "definition",
                       "reference", "none"]

#: Prefix of the escape hatch on axis D: ``other:<short label>``. Every use is reported
#: with its free text, so the vocabulary above can be corrected from what was needed
#: rather than from what was imagined.
OTHER_COMPONENT = "other:"

#: ENT-02 -- why an axis was left undecided. Closed vocabulary; ``ambiguous_scope`` is
#: the code for exactly the cases that appear as ``restricted`` today.
INDETERMINATE_REASONS = ["no_evidence", "ambiguous_scope", "conflicting_signals",
                         "outside_text"]

#: The value that triggers the reason requirement.
INDETERMINATE = "indeterminate"

#: Abstention lives on axes B and C, never on D: "which component is affected" is a
#: question about the text, not a judgement that can be left open.
ABSTENTION_AXES = ("semantic_status", "normative_direction")

#: Why a supplied axis value was discarded -- the phrasing used in ``pipeline_feedback``.
AXIS_VIOLATIONS = {
    "missing": "is missing",
    "outside_vocabulary": "lies outside the closed vocabulary",
    "not_a_list": "is not a list",
    "reason_missing": "abstains without a reason code (ENT-02)",
    "reason_without_abstention": "carries a reason code without an abstention",
}


def structural_operation(change: dict | None) -> str | None:
    """Axis A of a change record -- ``None`` for an unknown or absent ``kind``.

    Fails closed on purpose: a kind the mapping does not know is reported as "no
    operation" rather than as the nearest one, so a new kind shows up as a gap instead
    of quietly joining an existing bucket.
    """
    return STRUCTURAL_OPERATIONS.get((change or {}).get("kind"))


def _valid_component(value) -> bool:
    """Whether a single axis-D entry is admissible -- vocabulary or labelled ``other:``."""
    if not isinstance(value, str):
        return False
    if value.startswith(OTHER_COMPONENT):
        return bool(value[len(OTHER_COMPONENT):].strip())
    return value in AFFECTED_COMPONENTS


def check_axes(deutung: dict, change: dict | None = None) -> tuple[dict, list[dict]]:
    """The four axes of one interpretation, checked against their vocabularies.

    Returns the fields to write into the interpretation and the violations found. A
    value outside its vocabulary is **discarded and reported, never corrected**:
    reading ``restricted`` as ``narrowed`` or ``verschärft`` as ``tightened`` would
    measure the correction instead of the model, and the whole point of the additive
    introduction is to measure.

    Axis A comes from ``change`` and is not read from the answer at all -- the model
    never sees the field (see :data:`PIPELINE_OWNED_INTERPRETATION`).

    Axis D keeps the order it was delivered in ("most important first") and is filtered
    entry by entry; an empty result is ``None``, so "nothing usable was said" stays
    distinguishable from the explicit ``none``.

    ENT-02: ``indeterminate`` on axis B or C requires a code from
    :data:`INDETERMINATE_REASONS`. An abstention without one is not an abstention but a
    second way of saying nothing, so the axis is emptied and counted.
    """
    bad: list[dict] = []

    def reject(field: str, value, reason: str) -> None:
        bad.append({"field": field, "value": value, "reason": reason})

    fields: dict = {"structural_operation": structural_operation(change)}

    for field, vocabulary in (("semantic_status", SEMANTIC_STATUS),
                              ("normative_direction", NORMATIVE_DIRECTIONS)):
        value = deutung.get(field)
        if value in vocabulary:
            fields[field] = value
        else:
            fields[field] = None
            reject(field, value, "missing" if not value else "outside_vocabulary")

    supplied = deutung.get("affected_components")
    if not isinstance(supplied, list):
        fields["affected_components"] = None
        reject("affected_components", supplied,
               "missing" if supplied is None else "not_a_list")
    elif not supplied:
        fields["affected_components"] = None
        reject("affected_components", supplied, "missing")
    else:
        for c in supplied:
            if not _valid_component(c):
                reject("affected_components", c, "outside_vocabulary")
        fields["affected_components"] = [c for c in supplied if _valid_component(c)] or None

    reason = deutung.get("indeterminate_reason")
    reason = reason.strip() if isinstance(reason, str) else None
    if reason and reason not in INDETERMINATE_REASONS:
        reject("indeterminate_reason", reason, "outside_vocabulary")
        reason = None
    abstained = [f for f in ABSTENTION_AXES if fields[f] == INDETERMINATE]
    if abstained and not reason:
        for f in abstained:
            fields[f] = None
            reject(f, INDETERMINATE, "reason_missing")
    elif reason and not abstained:
        reject("indeterminate_reason", reason, "reason_without_abstention")
        reason = None
    fields["indeterminate_reason"] = reason or None
    return fields, bad

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
    "The interpretation axes are INDEPENDENT and are decided separately: semantic_status "
    "says what happens to the statement, normative_direction what it means for whoever is "
    "bound by it -- a smaller scope (narrowed) usually relaxes the burden, a narrower "
    "permission tightens it. Where the text itself does not settle one of the two, answer "
    "'indeterminate' with a reason code instead of guessing; an honest abstention is worth "
    "more than a forced label. "
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
    "semantic_status": "equivalent|clarified|extended|narrowed|replaced|contradictory|indeterminate -- what happens to the STATEMENT itself; narrowed = the scope now covers fewer cases, never 'stricter' (strictness is normative_direction)",
    "normative_direction": "tightened|relaxed|unchanged|not_applicable|indeterminate -- what it means for whoever is bound by the requirement; not_applicable for non-normative text",
    "affected_components": ["which parts of the standard the change touches, most important first: proof_obligation|limit_value|procedure|deadline|responsibility|documentation|scope|definition|reference|none; use 'other:<short label>' if none of them fits. Separation rules: proof_obligation when what changes is whether or to whom something must be proven, procedure when what changes is how (both may apply, then proof_obligation first); documentation for producing, keeping or presenting records with no body accepting them, proof_obligation as soon as a body accepts the proof; definition only for a change in the terms chapter or to a legal definition, with scope behind it if that shifts the scope of application indirectly; reference only when the change is nothing but the reference, otherwise the substantive component first and reference behind it"],
    "indeterminate_reason": "no_evidence|ambiguous_scope|conflicting_signals|outside_text -- required when semantic_status or normative_direction is indeterminate, '' otherwise",
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


# ------------------------------------------------------------------ field ownership
# Every field the pipeline knows itself is set by the pipeline (AP-07, ENT-51). A model
# that invents a key produces an error nobody downstream can recognize as one -- it looks
# like a missing chapter, which is how 165 interpretations of the 4110 run came to be
# uncheckable. So a pipeline-owned value that arrives in an answer is discarded and
# counted, never silently overwritten: the count is the only place that shows how often
# the model supplies something it was never asked for.
#
# Only ``section_id`` is actually part of the schema; everything else the model can
# supply unprompted alone. ``evidence_unique`` on a table or figure is the one value
# that used to survive, because check_asset_evidence does not compute it.

#: Pipeline-owned fields of a chapter answer.
PIPELINE_OWNED_CHAPTER = ("section_id", "mapping_id", "_source",
                          "_changes_total", "_changes_interpreted")

#: Pipeline-owned fields of a single interpretation.
PIPELINE_OWNED_INTERPRETATION = ("evidence_ok", "evidence_strict", "evidence_match_chars",
                                 "evidence_fragments", "evidence_unique",
                                 "evidence_span", "evidence_extraction",
                                 "change_index_best", "change_index_disputed",
                                 "structural_operation")

#: Pipeline-owned fields of a table or figure interpretation.
PIPELINE_OWNED_ASSET = ("evidence_ok", "evidence_strict", "evidence_match_chars",
                        "evidence_fragments", "evidence_unique",
                        "evidence_span", "evidence_extraction")


def drop_pipeline_owned(obj: dict, fields=PIPELINE_OWNED_INTERPRETATION) -> dict:
    """Remove every pipeline-owned field from a model answer; return what was removed.

    The return value is what makes the discarding visible -- see
    :func:`run_deutung`, which turns it into ``pipeline_feedback`` entries.
    """
    return {f: obj.pop(f) for f in fields if f in obj}


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


def cache_key(model: str, system: str, user: str) -> str:
    """The answer-cache key: SHA1 over model, system prompt and user prompt.

    Deliberately independent of the provider, so an already-cached answer stays valid
    when the backend changes. :class:`FixtureProvider` uses the same recipe -- a frozen
    answer is therefore bound to the exact prompt it was produced for.
    """
    return hashlib.sha1((model + "\x00" + system + "\x00" + user).encode()).hexdigest()


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
        # provider-independent. See :func:`cache_key`.
        return self.cache_dir / f"{cache_key(self.model, self.system, user)}.json"

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


# ------------------------------------------------------------------ provider seam
# The interpretation stage asks one question: "here are prompts, give me the answers".
# Everything else -- keys, retries, caching, batching -- is the provider's business.
# One method is therefore enough; ``live`` is the single flag the stage needs on top of
# it, because a run without answers reports itself as export mode.

@runtime_checkable
class DeutungProvider(Protocol):
    """Source of the per-chapter interpretations."""

    #: True when the provider actually produces answers (as opposed to prompt export).
    live: bool

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        """Answer every ``(tag, prompt)`` pair; ``None`` where no answer is available."""


class MissingFixtureError(KeyError):
    """Raised when a :class:`FixtureProvider` has no frozen answer for a prompt."""

    def __str__(self) -> str:                 # KeyError would quote the message
        return self.args[0]


class LiveProvider:
    """The existing path: an :class:`LlmClient`, in single or batch mode.

    Behaviour is unchanged in every respect -- same prompts, same cache, same batch
    logic. The class only moves the call site behind an interface.
    """

    def __init__(self, root: Path, model: str, cache_dir: Path, export_dir: Path,
                 provider: str = "anthropic", base_url: str | None = None,
                 key_env: str | None = None, api_version: str | None = None,
                 batch: bool = False, system: str | None = None):
        self.client = LlmClient(root, model, cache_dir, export_dir, provider=provider,
                                base_url=base_url, key_env=key_env,
                                api_version=api_version, system=system)
        self.batch = batch

    @property
    def live(self) -> bool:
        return self.client.live

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        if self.batch:
            return self.client.ask_json_batch(items)
        return {tag: self.client.ask_json(prompt, tag=tag) for tag, prompt in items}


class FixtureProvider:
    """Frozen answers from a JSON file, keyed exactly like the ``llm_cache``.

    For tests only. A prompt without a frozen answer raises
    :class:`MissingFixtureError` naming the missing key: the provider invents nothing
    and never falls back silently to the extractive summary, which would let a test
    pass while measuring nothing.
    """

    live = True

    def __init__(self, path, model: str = "", system: str = ""):
        self.path = Path(path)
        self.model = model
        self.system = system
        self.answers: dict = json.loads(self.path.read_text(encoding="utf-8"))

    def key(self, prompt: str) -> str:
        """The ``llm_cache`` key of a prompt under this provider's model and system."""
        return cache_key(self.model, self.system, prompt)

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict | None]:
        out: dict[str, dict | None] = {}
        for tag, prompt in items:
            k = self.key(prompt)
            if k not in self.answers:
                raise MissingFixtureError(
                    f"no frozen answer for chapter {tag!r} (key {k}) in {self.path} -- "
                    "regenerate the fixture, the prompt has changed")
            out[tag] = self.answers[k]
        return out


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


def _paras(secs: dict, ids) -> list[str]:
    """Paragraph text of the given sections -- the body a figure caption refers to."""
    out = []
    for sid in ids or []:
        s = secs.get(sid)
        if s:
            out += [p.get("n1") or p.get("n0") or "" for p in s.get("paragraphs") or []]
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
    """Text a table or figure interpretation may quote from.

    Captions and cells, **and the chapter's paragraph text**. The body belongs in here:
    a figure interpretation legitimately quotes the sentence that references the figure
    ("innerhalb der FRT-Grenzkurven nach Bild 17 aktiv"), and that sentence lives in the
    running text, not in a caption. Reviewing the 4110 run showed ten such quotes present
    verbatim in the document and rejected only because the haystack stopped at the
    caption.

    The scope stays the chapter -- wide enough for the referencing sentence, narrow
    enough that a quote from an unrelated chapter still fails.
    """
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
    parts += _paras(o_secs, ch.get("old_ids")) + _paras(n_secs, ch.get("new_ids"))
    return n2(" ".join(parts)).lower()


def check_asset_evidence(item: dict, hay: str) -> dict:
    """Evidence guard for table/figure interpretations, see :func:`check_evidence`."""
    return _guarded(item.get("evidence") or "", hay, _asset_evidence_ok)


#: Separators a rendered table row is built from -- see :func:`_render_cells`.
_ZELLTRENNER = re.compile(r"\s*[|‖]\s*")

#: Below this length a cell says nothing: "V", "1", "kW" match almost any table.
_ZELLE_MIN = 3


def _asset_evidence_ok(ev: str, hay: str) -> bool:
    """Whether a table or figure quote is covered by ``hay``.

    Two shapes have to pass, and they need different treatment:

    *Prose* -- a sentence from a caption or from the body. Checked verbatim, as before
    (whole quote, or its first 40 characters).

    *A rendered table row* -- ``"Punkt | Zeit | Schritt | V"``. This never occurs
    verbatim anywhere: the separators are added when the table is rendered for the
    prompt, the source only has the cells. Such a quote is split again and every cell
    of at least three characters has to be present. Shorter cells are dropped rather
    than counted -- "V" or "1" matches nearly any table and would let a wrong row pass.

    Deliberately **not** covered: a quote whose words all occur but in a different order.
    German moves the verb ("... reagieren müssen" versus "müssen ... reagieren"), and the
    model sometimes normalises it. Accepting that would mean accepting any permutation,
    including one that reverses the statement. Those stay a failed check and land in the
    review queue, where quote and source sit side by side.
    """
    if len(ev) < 8:
        return False
    if ev in hay or ev[:40] in hay:
        return True
    zellen = [z for z in (s.strip() for s in _ZELLTRENNER.split(ev)) if len(z) >= _ZELLE_MIN]
    if len(zellen) < 2:
        return False
    return all(z in hay for z in zellen)


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


# ------------------------------------------------------------------ quote extraction
# The guard used to check an answer as delivered, which measures formatting discipline
# instead of the ability to cite: on ``out/4110_haiku`` 21.7 % of the answers passed as
# delivered and 79.9 % once the quoted span was located -- 58 points lost to a label a
# model puts in front of a correct quote. Under quote-first [Gua26] the *system*
# localizes the span; it does not demand a pre-cleaned one from the model.
#
# Extraction runs on the n2()-normalized answer. n2 already unifies the typographic
# quotation marks („ “ ‚ ‘ » «) into their straight equivalents, so three patterns
# cover the six pairs -- including the mismatched ‚...' that Haiku writes in 300 of its
# 1612 answers.

#: Below this many characters a span is not drawn as a candidate. Without the floor a
#: quote around an arbitrary word would pass: the short-quote branch of
#: :func:`_evidence_ok` accepts a quote that covers a short record completely. The
#: floor bounds the *extraction*; the short-quote rule itself is untouched.
MIN_SPAN_CHARS = 15

#: Quotation mark pairs after n2(): straight double, straight single, and the single
#: guillemets, which n2 leaves alone. The character class excludes both marks, so a
#: match ends at the first closing mark.
_QUOTED = tuple(re.compile(re.escape(o) + "([^" + re.escape(o + c) + "]"
                           + "{" + str(MIN_SPAN_CHARS) + ",})" + re.escape(c))
                for o, c in (('"', '"'), ("'", "'"), ("›", "‹")))

#: A label a model puts in front of its quote. Closed list; ``:`` binds directly, a
#: dash needs spaces around it so that "Alt-Anlagen" stays a word (n2 has turned every
#: en dash into a hyphen by the time this runs).
_LABEL = re.compile(r"^(?:alte fassung|neue fassung|alt|neu|vorher|nachher)"
                    r"(?::\s*|\s+[-–]\s+)", re.IGNORECASE)


def evidence_candidates(answer: str) -> list[tuple[str, str]]:
    """Spans of a model answer that may carry the quote, with how each was won.

    In the order they are checked:

    ``roh``
        the whole answer -- what the guard did before AP-08. It stays the first
        candidate so that an answer passing today keeps passing, with the same span
        and the same numbers.
    ``anfuehrung``
        every span between quotation marks, in the order they appear. Several are
        possible and all are checked: an answer of the form ``ALT: '...'; NEU: '...'``
        quotes two versions, and either may be the one the record carries.
    ``etikett``
        the answer with a leading label removed -- only when there are no quotation
        marks, otherwise the label already sits in front of the first span.

    Spans shorter than :data:`MIN_SPAN_CHARS` are dropped; the whole answer never is.
    """
    text = n2(answer).strip()
    out = [(text, "roh")]
    quoted = sorted((m.start(), m.group(1).strip())
                    for p in _QUOTED for m in p.finditer(text))
    if quoted:
        out += [(s, "anfuehrung") for _, s in quoted if len(s) >= MIN_SPAN_CHARS]
    else:
        stripped = _LABEL.sub("", text, count=1).strip()
        if stripped != text and len(stripped) >= MIN_SPAN_CHARS:
            out.append((stripped, "etikett"))
    return out


def _guarded(answer: str, hay: str, ok) -> dict:
    """Run the guard over every candidate span; the first one that passes wins.

    ``evidence_span`` is the span the check passed with, ``evidence_extraction`` how
    it was won. The second field is what makes visible how often extraction was needed
    at all: for Sonnet it is nearly always ``roh``, and a rise reports a change in the
    model's or the prompt's shape rather than in its content.

    Where no candidate passes, the one covering the most characters is reported (ties
    to the earliest, so the whole answer wins over a span that covers no more). The
    verdict is the same either way; this only decides which span the diagnostics
    describe.
    """
    best = None
    for span, mode in evidence_candidates(answer):
        ev = span.lower()
        res = {"evidence_ok": ok(ev, hay), **evidence_metrics(ev, hay),
               "evidence_span": span, "evidence_extraction": mode}
        if res["evidence_ok"]:
            return res
        if best is None or res["evidence_match_chars"] > best["evidence_match_chars"]:
            best = res
    return best


def _record_haystack(c: dict) -> str:
    return " ".join(n2(c.get(k) or "").lower() for k in ("old_text", "new_text"))


def change_record(deutung: dict, ch: dict) -> dict | None:
    """The change record an interpretation addresses, ``None`` if its index names none.

    The one place that reads ``change_index``: an absent or out-of-range index yields
    no record, and everything built on it (the haystack of the evidence guard, axis A)
    fails closed instead of describing a foreign change.
    """
    i = deutung.get("change_index")
    if isinstance(i, int) and 0 <= i < len(ch["changes"]):
        return ch["changes"][i]
    return None


def change_haystack(deutung: dict, ch: dict) -> str:
    """Old and new text of the addressed change record, normalized and lower case."""
    c = change_record(deutung, ch)
    return _record_haystack(c) if c is not None else ""


def _evidence_unique(ev: str, deutung: dict, ch: dict) -> bool:
    """True when the quote is strict in the addressed record and in no other (ENT-30).

    A marker, not a gate: a quote that also fits a foreign record of the same chapter
    is correct as often as not, but it cannot localize the interpretation -- and that
    is the upper bound for undetectable ``change_index`` errors.
    """
    i = deutung.get("change_index")
    for j, c in enumerate(ch["changes"]):
        if j == i:
            continue
        if evidence_metrics(ev, _record_haystack(c))["evidence_strict"]:
            return False
    return True


def check_evidence(deutung: dict, ch: dict) -> dict:
    """Check an interpretation's quote against its change record.

    Returns the seven fields written into ``deutung.json``: ``evidence_ok`` (its rule
    unchanged), the diagnostics of :func:`evidence_metrics`, ``evidence_unique``
    (ENT-30) and the two of the extractor, ``evidence_span`` and
    ``evidence_extraction`` (AP-08).

    Every one of them is computed on the *extracted span*, not on the answer as
    delivered -- see :func:`evidence_candidates` for why.
    """
    hay = change_haystack(deutung, ch)
    res = _guarded(deutung.get("evidence") or "", hay, _evidence_ok)
    res["evidence_unique"] = (res["evidence_strict"]
                              and _evidence_unique(res["evidence_span"].lower(),
                                                   deutung, ch))
    return res


#: Why an interpretation is in the review queue, in a fixed order. The first two are the
#: historical triggers and keep their meaning exactly (ENT-18); ``change_index_disputed``
#: is added by AP-07. The queue is extended, not rebuilt.
REVIEW_REASONS = ("contradiction_flag", "evidence_ok", "change_index_disputed")


def _review_reasons(deutung: dict) -> list[str]:
    """The reasons this interpretation needs a human look; empty means it does not."""
    return [r for r in REVIEW_REASONS
            if (not deutung["evidence_ok"] if r == "evidence_ok" else deutung.get(r))]


def change_index_check(deutung: dict, ch: dict) -> dict:
    """Cross-check the chosen ``change_index`` against every record of the chapter (AP-07).

    ``evidence_match_chars`` is computed not only for the *chosen* record but for all of
    them. The strongest candidate is reported as ``change_index_best``; if it is
    *strictly* better than the chosen one, ``change_index_disputed`` is true.

    Nothing is corrected. The model chooses which change its interpretation is about,
    and it can be right for a reason text similarity cannot see -- it may interpret a
    connection instead of quoting it. An automatic correction would replace a right
    choice with a more similar one. The flag goes into the review queue, exactly as
    ``evidence_unique`` does (ENT-30): mark, do not decide.

    A tie is no contradiction: ``change_index_best`` then names the lowest of the tied
    indices (deterministic) and ``change_index_disputed`` stays false. Where no record
    supports the quote at all -- because there is no quote, or because it appears in
    none of them -- ``change_index_best`` is ``None``: naming record 0 would be an
    assertion without evidence, which is the error class this whole package closes.
    """
    # the span the guard settled on, not the answer as delivered: scoring the shell
    # against every record would measure the label, and the subset relation to
    # evidence_unique (both read the same quote) would quietly break (AP-08)
    ev = n2(deutung.get("evidence_span") or deutung.get("evidence") or "").lower()
    scores = [evidence_metrics(ev, _record_haystack(c))["evidence_match_chars"]
              for c in ch["changes"]]
    best = max(scores, default=0)
    if best == 0:
        return {"change_index_best": None, "change_index_disputed": False}
    i = deutung.get("change_index")
    # an absent or out-of-range index counts as covering nothing, so a record that does
    # cover something disputes it -- the same fail-closed rule as change_haystack()
    chosen = scores[i] if isinstance(i, int) and 0 <= i < len(scores) else -1
    return {"change_index_best": scores.index(best),
            "change_index_disputed": best > chosen}


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
                language: str = "de", title: str = "",
                deutung_provider: DeutungProvider | None = None) -> dict:
    """Per-chapter interpretation. scope=core: chapters with substantive changes.
    provider/base_url/key_env/api_version steer the LLM backend; without a key the
    prompts are exported and summaries fall back to extractive.
    batch=True submits all uncached chapters in one Anthropic message batch (~50% cheaper,
    asynchronous). The JSON schema is neutral English; `language` decides in which language
    the free-text values are written, `title` names the compared standard in the prompt.
    deutung_provider replaces the source of the answers (see :class:`DeutungProvider`);
    the default is a :class:`LiveProvider` built from the arguments above, so existing
    callers are unaffected."""
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    Path(out_dir).mkdir(parents=True, exist_ok=True)   # LiveProvider used to do this
    answer_source = deutung_provider or LiveProvider(
        root, model, out_dir / "llm_cache", out_dir / "llm_prompts",
        provider=provider, base_url=base_url, key_env=key_env, api_version=api_version,
        batch=batch, system=build_system_prompt(language, title))

    def sec_excerpt(secs, ids):
        txt = []
        for sid in ids:
            s = secs.get(sid)
            if s:
                txt.append(" ".join(p.get("n1", p.get("n0", "")) for p in s["paragraphs"]))
        return " ".join(txt)

    # ---- phase 1: build one prompt per in-scope chapter --------------------------
    jobs = []          # (ch, cid, mid, tag, prompt, sel, old_x, new_x)
    for ch in synopse["chapters"]:
        substantive = [c for c in ch["changes"] if not c.get("semantic_equal")]
        if scope == "core" and not substantive and not ch.get("part_changed"):
            continue
        old_x = sec_excerpt(o_secs, ch.get("old_ids") or [])
        new_x = sec_excerpt(n_secs, ch.get("new_ids") or [])
        prompt, sel = build_chapter_prompt(ch, old_x, new_x, o_secs, n_secs)
        cid = ch.get("new_id") or ch.get("old_id")
        # the tag routes the answer back to its chapter and names the exported prompt
        # file, so it has to be unique: it is built from the mapping id (ENT-24)
        mid = ch.get("mapping_id") or str(cid)
        jobs.append((ch, cid, mid, re.sub(r"[^\w.]", "_", mid), prompt, sel, old_x, new_x))

    # ---- phase 2: resolve the answers --------------------------------------------
    # The provider decides how: batch mode submits every uncached chapter in one
    # asynchronous batch (~50% cheaper), otherwise the chapters are asked one by one.
    answers = answer_source.resolve([(j[3], j[4]) for j in jobs])

    # ---- phase 3: assemble, guard the evidence, collect the review queue ----------
    results = []
    review = []
    dropped: list[dict] = []       # pipeline-owned values the model supplied anyway
    n_llm = 0
    for ch, cid, mid, tag, prompt, sel, old_x, new_x in jobs:
        data = answers.get(tag)
        drops = Counter()          # field -> how often it arrived in this chapter
        supplied = {}              # field -> the discarded value, for the report
        invalid = Counter()        # (axis field, violation) -> how often in this chapter
        offenders: dict = {}       # (axis field, violation) -> the discarded values
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
            # class P: whatever the model wrote into a pipeline-owned field is discarded
            # here, before anything reads it, and counted below (AP-07)
            supplied = drop_pipeline_owned(data, PIPELINE_OWNED_CHAPTER)
            drops.update(supplied.keys())
            data["_source"] = "llm"
            for d in data.get("interpretations", []):
                drops.update(drop_pipeline_owned(d, PIPELINE_OWNED_INTERPRETATION).keys())
                # ENT-01: axis A from the change record, axes B/C/D checked against
                # their vocabularies. A rejected value is discarded and counted below,
                # never corrected -- see check_axes
                axes, violations = check_axes(d, change_record(d, ch))
                d.update(axes)
                for v in violations:
                    key = (v["field"], v["reason"])
                    invalid[key] += 1
                    offenders.setdefault(key, []).append(v["value"])
                d.update(check_evidence(d, ch))
                # class V: the model's change_index stays as it is, the cross-check only
                # says whether another record of the chapter fits the quote better
                d.update(change_index_check(d, ch))
                # the review queue still keys on evidence_ok (ENT-18): it switches to
                # evidence_strict once the difference between both is quantified.
                # change_index_disputed is an additional trigger, with its own reason.
                reasons = _review_reasons(d)
                if reasons:
                    review.append({"section_id": cid, "mapping_id": mid,
                                   "review_reasons": reasons, **d})
            # table/figure interpretations: check evidence against cells/captions
            if data.get("tables") or data.get("figures"):
                hay = asset_haystack(ch, o_secs, n_secs)
                for kind in ("tables", "figures"):
                    for a in (data.get(kind) or []):
                        drops.update(drop_pipeline_owned(a, PIPELINE_OWNED_ASSET).keys())
                        a.update(check_asset_evidence(a, hay))
                        if not a["evidence_ok"]:
                            review.append({"section_id": cid, "mapping_id": mid,
                                           "asset": kind[:-1],
                                           "review_reasons": ["evidence_ok"], **a})
        # Which chapter this answer belongs to is decided here, not in the answer: the
        # model is asked to echo section_id and in the 4110 run it did not -- it wrote a
        # slug of the heading, or the value of the "Teil" field for unnumbered annexes,
        # which left 165 interpretations (10.6 %) without a chapter to check them against.
        data["mapping_id"] = mid
        data["section_id"] = cid
        data["_changes_total"] = len(ch["changes"])
        data["_changes_interpreted"] = sel if data.get("_source") == "llm" else []
        results.append(data)
        for field, n in sorted(drops.items()):
            was = supplied.get(field)
            dropped.append({
                "section_id": cid, "mapping_id": mid, "phase": "deutung",
                "field": field, "count": n,
                "finding": (f"the model supplied the pipeline-owned field "
                            f"'{field}' {n}x; the value was discarded"
                            + (f" (it said {was!r}, the pipeline says {cid!r})"
                               if field == "section_id" and was != cid else "")),
            })
        # ... and what the model wrote into an axis it does own, outside the vocabulary
        # it was given (ENT-01). Counted, not repaired: a quietly corrected answer would
        # make the measurement of the new taxonomy measure the correction.
        for (field, reason), n in sorted(invalid.items()):
            seen = list(dict.fromkeys(str(v) for v in offenders[(field, reason)]))
            dropped.append({
                "section_id": cid, "mapping_id": mid, "phase": "deutung",
                "field": field, "reason": reason, "count": n,
                "finding": (f"the axis field '{field}' {AXIS_VIOLATIONS[reason]} in {n} "
                            f"interpretation(s): {seen[:5]}; discarded, not corrected"),
            })

    # aggregate preprocessing feedback -> feed it back to the pipeline phases
    feedback = []
    for r in results:
        for fb in r.get("pipeline_feedback") or []:
            feedback.append({"section_id": r.get("section_id"), **fb})
    # ... and the pipeline's own feedback about the answers: what the model wrote into a
    # field it does not own (AP-07). Five invented chapter ids made 165 interpretations
    # uncheckable in the 4110 run, and nothing in the output said so.
    feedback += dropped
    live = answer_source.live
    out = {"model": model if live else None, "mode": "api" if live else "export",
           "language": language,
           "n_chapters": len(results), "n_llm": n_llm, "chapters": results,
           "review_queue": review, "pipeline_feedback": feedback}
    (out_dir / "deutung.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    if feedback:
        L = ["# Vorverarbeitungs-Feedback aus der KI-Deutung\n",
             "_Von der Deutung gemeldete Artefakte — Kandidaten für Verbesserungen "
             "in Ingest/Alignment/Diff (Phase in Klammern). Phase `deutung`: Felder, "
             "die das Modell geliefert hat, obwohl die Pipeline sie selbst setzt._\n"]
        by_phase = Counter(fb["phase"] for fb in feedback)
        L.append("**Verteilung:** " + ", ".join(f"{p}: {n}" for p, n in by_phase.most_common()) + "\n")
        for fb in feedback:
            idx = f" (Änderungen {fb['change_indices']})" if fb.get("change_indices") else ""
            L.append(f"- **{fb['section_id']}** [{fb['phase']}]{idx}: {fb['finding']}")
        (out_dir / "pipeline_feedback.md").write_text("\n".join(L), encoding="utf-8")
    return out
