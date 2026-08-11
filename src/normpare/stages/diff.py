"""
diffs.py -- stage S4: diff levels (strictly separated).

syntactic (N1): exact character/token operations + statistics
    {chars_inserted, chars_deleted, chars_equal, change_ratio, ops[...]}
semantic (N3): equal on N3 => "cosmetic"; otherwise "changed" with
    value diff (value level), modality shift (per-sentence max), reference diff.
Additionally per chapter: formula diff (normalized LaTeX/linear strings) and
table diff (caption matching + cell comparison).

Output: synopse.json -- chapter records with "changes" in the order of the new version.
"""
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from .align.sections import mapping_id
from .enrich import modality, refs, values
from ..text.textnorm import compare_key, garbage_ratio, n1, n2, n3

_TOKEN = re.compile(r"(\s+|[.,;:!?(){}\[\]/–—„“”\"'])")
_ALNUM = re.compile(r"[0-9A-Za-zÄÖÜäöüß]")


def _tokens(t: str):
    return [x for x in _TOKEN.split(t) if x != ""]


def syntactic_diff(old_n1: str, new_n1: str) -> dict:
    """Token diff on N1 with exact character statistics."""
    a, b = _tokens(old_n1), _tokens(new_n1)
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    ops = []
    ci = cd = ce = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            seg = "".join(a[i1:i2])
            ce += len(seg)
            ops.append(("=", seg))
        else:
            if op in ("delete", "replace"):
                seg = "".join(a[i1:i2])
                cd += len(seg)
                if seg:
                    ops.append(("-", seg))
            if op in ("insert", "replace"):
                seg = "".join(b[j1:j2])
                ci += len(seg)
                if seg:
                    ops.append(("+", seg))
    total = ci + cd + ce
    return {"chars_inserted": ci, "chars_deleted": cd, "chars_equal": ce,
            "change_ratio": round((ci + cd) / total, 4) if total else 0.0,
            "ops": ops}


_FUNCTION_WORDS = re.compile(
    r"\b(der|die|das|und|oder|mit|für|nach|von|vor|ist|sind|wird|werden|eine?n?|zu|bei|"
    r"im|am|auf|des|dem|den|nicht|kein|keine|muss|müssen|darf|dürfen|soll|sollte|kann|"
    r"können|gilt|gelten|sowie|bzw|als|aus|über|unter|bis|durch|wenn|dass)\b", re.I)


def _is_substantive(seg: str) -> bool:
    """Does the operation text carry content?

    Always substantive: parameter values (number+unit), function/negation words, long
    words, longer passages. Not substantive: punctuation/whitespace, short symbol/index
    fragments, glyph garbage.
    """
    s = seg.strip()
    if not _ALNUM.search(s):
        return False
    if garbage_ratio(s) > 0.4:
        return False
    if values.extract_values(s):
        return True                     # never filter out parameter values
    if "…" in s:
        return False                    # garbage marker from broken glyphs
    if _FUNCTION_WORDS.search(s):
        return True
    if re.search(r"[A-Za-zÄÖÜäöüß]{7,}", s):
        return True                     # real (long) word
    if len(s) > 50:
        return True
    return False                        # short symbol/index fragments


def display_ops(ops: list) -> tuple[list, bool]:
    """Display diff: degrade cosmetic operations to 'equal'.

    Rules (the display then shows the NEW variant):
      1. adjacent (-,+) pair with the same compare_key  -> equal (hyphenation etc.)
      2. glyph garbage is always removed
      3. a balanced replacement pair is kept marked; unbalanced pairs run through the
         per-side substance filter.
    """
    def _residue(s: str) -> bool:
        """Glyph garbage from PDF extraction -- never meaningfully displayable."""
        return "…" in s or garbage_ratio(s) > 0.2

    # group into replacement blocks (contiguous -/+ runs)
    out, i = [], 0
    while i < len(ops):
        if ops[i][0] == "=":
            out.append(ops[i])
            i += 1
            continue
        block = []
        while i < len(ops) and ops[i][0] != "=":
            block.append(ops[i])
            i += 1
        # 1) exactly cosmetic pairs (same compare_key) -> equal
        dels = [s for o, s in block if o == "-"]
        inss = [s for o, s in block if o == "+"]
        if dels and inss and compare_key("".join(dels)) == compare_key("".join(inss)):
            out.append(("=", "".join(inss)))
            continue
        # 2) always remove glyph garbage (drop the deletion, show the insertion neutrally)
        kept = []
        for o, s in block:
            if _residue(s):
                if o == "+":
                    kept.append(("=", s))
            else:
                kept.append((o, s))
        # 3) balanced replacement pair -> keep it marked (e.g. a change of reference quantity
    #    is meaningful). Unbalanced pairs (formula remnant vs. single char) run through
    #    the per-side substance filter.
        dtxt = " ".join(s.strip() for o, s in kept if o == "-").strip()
        itxt = " ".join(s.strip() for o, s in kept if o == "+").strip()
        balanced = (len(dtxt) >= 2 and len(itxt) >= 2
                    and max(len(dtxt), len(itxt)) <= 4 * min(len(dtxt), len(itxt))
                    and len(dtxt.split()) <= 3 and len(itxt.split()) <= 3)
        for o, s in kept:
            if o == "=" or balanced or _is_substantive(s):
                out.append((o, s))
            elif o == "+":
                out.append(("=", s))
            # non-substantive one-sided deletion: drop it
    # merge contiguous '=' segments
    merged = []
    for op, seg in out:
        if merged and merged[-1][0] == op == "=":
            merged[-1] = ("=", merged[-1][1] + seg)
        else:
            merged.append((op, seg))
    substantive = any(op != "=" for op, _ in merged)
    return merged, substantive


def _mod_max(paras: list[dict]) -> str:
    best = "informativ"
    for p in paras:
        m = p.get("modality", "informativ")
        if modality.RANK.get(m, 0) > modality.RANK.get(best, 0):
            best = m
    return best


def _join(paras: list[dict]) -> str:
    return "\n".join(p.get("n1") or n1(p.get("n0", "")) for p in paras)


def _refs_diff(olds: list[dict], news: list[dict]) -> dict:
    oi = set().union(*[set(p.get("refs_internal", [])) for p in olds]) if olds else set()
    ni = set().union(*[set(p.get("refs_internal", [])) for p in news]) if news else set()
    oe = set().union(*[set(p.get("refs_external", [])) for p in olds]) if olds else set()
    ne = set().union(*[set(p.get("refs_external", [])) for p in news]) if news else set()
    return {"internal_added": sorted(ni - oi), "internal_removed": sorted(oi - ni),
            "external_added": sorted(ne - oe), "external_removed": sorted(oe - ne)}


def _formula_key(f: dict) -> str:
    s = f.get("latex") or f.get("linear") or ""
    return re.sub(r"\s+", "", s)


def formulas_diff(old_sec_list, new_sec_list) -> dict:
    of = [f for s in old_sec_list for f in s.get("formulas", [])]
    nf = [f for s in new_sec_list for f in s.get("formulas", [])]
    ok = {_formula_key(f): f for f in of if _formula_key(f)}
    nk = {_formula_key(f): f for f in nf if _formula_key(f)}
    # sorted set differences -> deterministic order (reproducibility; v2 iterated a raw set)
    added = [nk[k] for k in sorted(nk.keys() - ok.keys())]
    removed = [ok[k] for k in sorted(ok.keys() - nk.keys())]
    return {"n_old": len(of), "n_new": len(nf),
            "added": [{"id": f["id"], "repr": f.get("latex") or f.get("linear")} for f in added],
            "removed": [{"id": f["id"], "repr": f.get("latex") or f.get("linear")} for f in removed]}


def _is_fragment_table(t: dict) -> bool:
    """Exclude captionless degenerate 'tables' (extracted equation numbers/formula
    remnants, e.g. '(11)') from the diff view. REAL captionless tables (many cells,
    e.g. an abbreviation list) are kept.
    """
    if (t.get("caption") or "").strip():
        return False
    if t.get("n_cols", 0) < 2:
        return True
    ne = sum(1 for r in t.get("cells") or [] for c in r if (c or "").strip())
    return ne <= 2


def tables_diff(old_sec_list, new_sec_list, sim_backend) -> list[dict]:
    ot = [t for s in old_sec_list for t in s.get("tables", []) if not _is_fragment_table(t)]
    nt = [t for s in new_sec_list for t in s.get("tables", []) if not _is_fragment_table(t)]
    out = []
    used_o = set()
    for t_new in nt:
        best, bs = None, 0.0
        for i, t_old in enumerate(ot):
            if i in used_o:
                continue
            ca, cb = t_old.get("caption") or "", t_new.get("caption") or ""
            if ca and cb:
                s = SequenceMatcher(None, n3(ca), n3(cb)).ratio()
            else:
                fa = " ".join(" ".join(r) for r in t_old.get("cells", [])[:3])
                fb = " ".join(" ".join(r) for r in t_new.get("cells", [])[:3])
                s = SequenceMatcher(None, n3(fa)[:400], n3(fb)[:400]).ratio() * 0.9
            if s > bs:
                best, bs = i, s
        if best is not None and bs >= 0.55:
            used_o.add(best)
            t_old = ot[best]
            oc = ["\t".join(r) for r in t_old.get("cells", [])]
            nc = ["\t".join(r) for r in t_new.get("cells", [])]
            sm = SequenceMatcher(a=[n3(r) for r in oc], b=[n3(r) for r in nc], autojunk=False)
            changed_rows = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes()
                               if op != "equal")
            out.append({"kind": "matched", "old": t_old["id"], "new": t_new["id"],
                        "caption": t_new.get("caption") or t_old.get("caption"),
                        "rows_changed": changed_rows,
                        "identical": changed_rows == 0,
                        "confidence": round(bs, 3)})
        else:
            out.append({"kind": "new", "new": t_new["id"], "caption": t_new.get("caption")})
    for i, t_old in enumerate(ot):
        if i not in used_o:
            out.append({"kind": "removed", "old": t_old["id"], "caption": t_old.get("caption")})
    return out


def _para_change(kind: str, olds: list[dict], news: list[dict], conf: float,
                 extra: dict | None = None) -> dict:
    old_t, new_t = _join(olds), _join(news)
    rec = {"kind": kind, "confidence": conf,
           "old_ids": [p["id"] for p in olds], "new_ids": [p["id"] for p in news],
           "old_text": old_t or None, "new_text": new_t or None}
    term_no = next((p.get("term_no") for p in news + olds if p.get("term_no")), None)
    if term_no:
        rec["term_no"] = term_no
    if olds and news:
        rec["syntactic"] = syntactic_diff(n1(old_t), n1(new_t))
        dops, substantive = display_ops(rec["syntactic"]["ops"])
        rec["display_ops"] = dops
        if compare_key(old_t) == compare_key(new_t) or not substantive:
            if kind in ("similar", "identical"):
                rec["kind"] = "cosmetic"
            rec["semantic_equal"] = True
        else:
            rec["semantic_equal"] = False
        rec["kennwerte"] = values.diff_values(old_t, new_t)
        om, nm = _mod_max(olds), _mod_max(news)
        rec["modality"] = {"old": om, "new": nm, "shift": modality.shift(om, nm)}
        rec["refs"] = _refs_diff(olds, news)
    elif news:
        rec["modality"] = {"new": _mod_max(news)}
        rec["kennwerte"] = {"added": values.extract_values(new_t), "changed": [], "removed": []}
    elif olds:
        rec["modality"] = {"old": _mod_max(olds)}
        rec["kennwerte"] = {"removed": values.extract_values(old_t), "changed": [], "added": []}
    if extra:
        rec.update(extra)
    return rec


# ADR-0002: short glossary/heading fragments in term/abbreviation sections are not real
# deletions; keep them out of the "removed" stream. Uses several signals together (section/
# kind context + short + no sentence end + no value/reference), never length alone.
_TERM_SECTION_RE = re.compile(r"begriff|abk[uü]rzung|symbol|formelzeichen", re.I)


def _is_non_normative(old_paras: list[dict], chapter_title) -> bool:
    """True if the removed paragraph(s) are a non-normative glossary/heading fragment.

    Combines several signals (never length alone): a short fragment with no sentence end
    that is either classified as a term/heading name at ingest, or sits in a
    term/abbreviation section and carries no parameter value and no reference.
    """
    text = " ".join((p.get("n1") or p.get("n0") or "") for p in old_paras).strip()
    if not text or len(text.split()) > 7 or re.search(r"[.!?]\s*$", text):
        return False
    # strong signal: ingest classified this as a term name or a heading fragment
    if {p.get("kind") for p in old_paras} & {"term", "heading"}:
        return True
    # otherwise only within a term/abbreviation section, and only if it carries no
    # parameter value and no reference (protects short normative statements)
    if _TERM_SECTION_RE.search(chapter_title or ""):
        return not (values.extract_values(text) or refs.internal(text) or refs.external(text))
    return False


def build_synopse(old_doc, new_doc, mapping_records, sim_backend, out_path: str | Path,
                  pair_label: str) -> dict:
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    o_par = {p["id"]: p for s in old_doc["sections"] for p in s["paragraphs"]}
    n_par = {p["id"]: p for s in new_doc["sections"] for p in s["paragraphs"]}

    chapters = []
    for rec in mapping_records:
        mt = rec["match_type"]
        old_ids = rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else [])
        new_ids = rec.get("new_ids") or ([rec["new_id"]] if rec.get("new_id") else [])
        ch = {"mapping_id": rec.get("mapping_id") or mapping_id(old_ids, new_ids),
              "old_id": rec.get("old_id"), "new_id": rec.get("new_id"),
              "old_ids": old_ids, "new_ids": new_ids,
              "title": rec.get("new_title") or rec.get("old_title"),
              "level": rec.get("new_level") or rec.get("old_level"),
              "part": rec.get("new_part") or rec.get("old_part"),
              "part_changed": rec.get("part_changed", False),
              "mode": mt, "map_confidence": rec.get("confidence"),
              "changes": [], "n_identical": 0}

        if mt == "new":
            for p in n_secs.get(rec["new_id"], {"paragraphs": []})["paragraphs"]:
                if (p.get("n1") or p.get("n0", "")).strip() and p.get("kind") != "formula":
                    ch["changes"].append(_para_change("new", [], [p], 0.0))
        elif mt == "removed":
            for p in o_secs.get(rec["old_id"], {"paragraphs": []})["paragraphs"]:
                if (p.get("n1") or p.get("n0", "")).strip() and p.get("kind") != "formula":
                    if _is_non_normative([p], ch["title"]):
                        ch["n_non_normative"] = ch.get("n_non_normative", 0) + 1
                        continue
                    ch["changes"].append(_para_change("removed", [p], [], 0.0))
        else:
            for l in rec.get("para_links", []):
                olds = [o_par[i] for i in l["old_ids"]]
                news = [n_par[j] for j in l["new_ids"]]
                if l["kind"] == "identical":
                    ch["n_identical"] += 1
                    continue
                extra = {}
                if l["kind"] == "moved_away":
                    extra["moved_to"] = l.get("moved_to")
                if l["kind"] == "moved_in":
                    extra["moved_from"] = l.get("moved_from")
                c = _para_change(l["kind"], olds, news, l.get("confidence", 0.0), extra)
                if c["kind"] == "cosmetic" and c.get("syntactic", {}).get("change_ratio", 0) == 0:
                    ch["n_identical"] += 1
                    continue
                # noise filter: new/removed mini or garbage fragments
        # (PDF artifacts like "19", "k S " or pure formula remnants)
                if c["kind"] in ("new", "removed"):
                    t = c.get("new_text") or c.get("old_text") or ""
                    if len(compare_key(t)) < 4 or garbage_ratio(t) > 0.4:
                        ch.setdefault("n_noise_skipped", 0)
                        ch["n_noise_skipped"] += 1
                        continue
                if c["kind"] == "removed" and _is_non_normative(olds, ch["title"]):
                    ch["n_non_normative"] = ch.get("n_non_normative", 0) + 1
                    continue
                ch["changes"].append(c)

        old_sec_list = [o_secs[i] for i in old_ids if i in o_secs]
        new_sec_list = [n_secs[i] for i in new_ids if i in n_secs]
        ch["formulas_diff"] = formulas_diff(old_sec_list, new_sec_list)
        ch["tables_diff"] = tables_diff(old_sec_list, new_sec_list, sim_backend)
        chapters.append(ch)

    from collections import Counter
    counts = Counter(c["kind"] for ch in chapters for c in ch["changes"])
    result = {"pair": pair_label,
              "old_doc": old_doc["doc_id"], "new_doc": new_doc["doc_id"],
              "stats": dict(counts),
              "chapters": chapters}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
