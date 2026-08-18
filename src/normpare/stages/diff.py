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
from .review_removed import annotate_relocation
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


#: What a caption looks like: keyword, number, separator, descriptive text. The number may
#: be an annex letter ("A.1"); the separator is en/em dash, hyphen or colon -- a blank is
#: deliberately not one, because "Tabelle 10 empfohlen." is a sentence end, not a caption.
_CAPTION_RE = re.compile(
    r"\s*(?:Tabellen?|Tab\.|Bild(?:er)?|Abbildung|Table|Figure|Fig\.)\s*"
    r"([0-9]+(?:\.[0-9]+)*|[A-Za-z](?:\.[0-9]+)*)\s*[–—:-]\s*(\S.*)", re.I)
#: Below this many characters the descriptive rest is not a description.
MIN_CAPTION_TEXT = 3
#: Rows and characters of the cell text that enter the content comparison. AP-22 widened
#: the window to ten rows; AP-23 measured what that costs and takes it back. The head of a
#: table carries its identity, the body does not: in a form the first rows are the header
#: both editions share and everything below is filled in, so the wider the window, the more
#: it compares entries instead of tables (4110 B.11.2 0.850 -> 0.407, E.13 0.954 -> 0.287).
#: Measured over all three corpora, the narrow window loses none of the AP-22 repairs --
#: 10.3.4 and 10.3.5 pair the same way at both widths, because that repair came from
#: comparing the caption *without its number* -- and it wins back six pairings at 4110, six
#: at 4120 and one at 60909 (runs/AP-23_2026-08-18/fenster_*.txt).
CONTENT_ROWS = 3
CONTENT_CHARS = 400
#: From this score on an assigned pair counts as the same table. Applied *after* the
#: assignment: a pair below it falls apart into "removed" + "new".
TABLE_MATCH_MIN = 0.55
#: The same for a pair found across chapter boundaries. Higher than TABLE_MATCH_MIN,
#: because a move is the stronger claim: inside a chapter the two tables are already known
#: to belong to the same subject, across the whole document they are not.
CROSS_CHAPTER_MIN = 0.80


def caption_text(caption: str | None) -> str | None:
    """The descriptive part of a table/figure caption, or ``None`` if the string is not one.

    Two jobs in one place (AP-22). First hygiene: PDF extraction regularly puts the tail of
    the preceding sentence into the caption field ("Tabelle 10 empfohlen."), and because
    that field was not empty, the caption comparison ran on garbage. Such a string is
    treated like a missing caption -- it is not deleted, it just does not count.

    Second, the number is dropped: in a renumbered edition it is the least reliable part of
    the caption while the descriptive rest is the carrier ("Tabelle 11 - Einstellwerte X"
    and "Tabelle 17 - Einstellwerte X" are the same table).
    """
    m = _CAPTION_RE.fullmatch(caption or "")
    if not m:
        return None
    text = m.group(2).strip()
    return text if len(text) >= MIN_CAPTION_TEXT else None


def _content_key(t: dict) -> str:
    rows = (t.get("cells") or [])[:CONTENT_ROWS]
    return n3(" ".join(" ".join(r) for r in rows))[:CONTENT_CHARS]


def table_similarity(t_old: dict, t_new: dict) -> float:
    """How much two tables look like the same table: the stronger of both signals.

    Caption (without its number) and cell content are always both measured and the higher
    value wins. The content used to be a fallback reached only when a caption was missing,
    and carried a 0.9 penalty for being one; both are gone -- a table whose caption was
    rewritten is still recognizable by its rows, and vice versa.
    """
    ca, cb = caption_text(t_old.get("caption")), caption_text(t_new.get("caption"))
    by_caption = SequenceMatcher(None, n3(ca), n3(cb)).ratio() if ca and cb else 0.0
    fa, fb = _content_key(t_old), _content_key(t_new)
    by_content = SequenceMatcher(None, fa, fb).ratio() if fa and fb else 0.0
    return max(by_caption, by_content)


def _assign_tables(ot: list[dict], nt: list[dict]) -> dict[int, tuple[int, float]]:
    """Globally assign old to new tables (Hungarian), keyed by the index of the new table.

    The greedy predecessor walked the new tables in document order and took the best free
    old partner for each, so an early table could take a partner that a later one needed
    (4110/10.3.4: new table 16 took old table 11 before new 17 was ever asked). The
    paragraph aligner has solved the same problem with an optimal assignment all along;
    this uses its wrapper, and with it SciPy, which the project already depends on.
    """
    if not ot or not nt:
        return {}
    # local import: keeps the SciPy/numpy import of align.paras out of module load
    from .align.paras import _lsa

    sim = [[table_similarity(t_old, t_new) for t_new in nt] for t_old in ot]
    rows, cols = _lsa([[-s for s in row] for row in sim])
    return {int(j): (int(i), sim[i][j]) for i, j in zip(rows, cols)
            if sim[i][j] >= TABLE_MATCH_MIN}


def _rows_changed(t_old: dict, t_new: dict) -> int:
    """Rows the new edition added, dropped or rewrote -- the row diff of two paired tables."""
    oc = ["\t".join(r) for r in t_old.get("cells", [])]
    nc = ["\t".join(r) for r in t_new.get("cells", [])]
    sm = SequenceMatcher(a=[n3(r) for r in oc], b=[n3(r) for r in nc], autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def cross_chapter_tables(chapters: list[dict], old_tables: dict[str, dict],
                         new_tables: dict[str, dict]) -> list[dict]:
    """Document-wide post-pass: a table that changed chapters is a move, not two events.

    :func:`tables_diff` compares the tables of *one* chapter mapping, so a table that sits
    in 11.4.21 in the old edition and in 11.4.24 in the new one is never even put next to
    its counterpart -- however good the measure is, it is counted once as a deletion and
    once as an addition (4110: two identical certificate tables, similarity 1.000 and
    0.995). This pass runs after all chapters are paired, puts every leftover ``removed``
    against every leftover ``new`` of the *whole* document and assigns them globally, like
    the paragraph aligner does with ``moved_away``/``moved_in``.

    Additive: two values are added to ``kind``, the three existing ones keep their meaning,
    and a record only changes when a partner above :data:`CROSS_CHAPTER_MIN` is found.
    ``chapters`` is modified in place; the return value lists the moves.
    """
    # local import: keeps the SciPy/numpy import of align.paras out of module load
    from .align.paras import _lsa

    rem, add = [], []          # (chapter, index in its tables_diff, table dict)
    for ch in chapters:
        for k, r in enumerate(ch.get("tables_diff") or []):
            if r["kind"] == "removed" and r.get("old") in old_tables:
                rem.append((ch, k, old_tables[r["old"]]))
            elif r["kind"] == "new" and r.get("new") in new_tables:
                add.append((ch, k, new_tables[r["new"]]))
    if not rem or not add:
        return []

    sim = [[table_similarity(t_old, t_new) for _, _, t_new in add] for _, _, t_old in rem]
    rows, cols = _lsa([[-s for s in row] for row in sim])
    moves = []
    for a, b in zip(rows, cols):
        score = sim[a][b]
        if score < CROSS_CHAPTER_MIN:
            continue
        ch_old, k_old, t_old = rem[a]
        ch_new, k_new, t_new = add[b]
        changed = _rows_changed(t_old, t_new)
        common = {"old": t_old["id"], "new": t_new["id"], "rows_changed": changed,
                  "identical": changed == 0, "confidence": round(score, 3)}
        ch_old["tables_diff"][k_old] = {
            "kind": "moved_away", "caption": ch_old["tables_diff"][k_old].get("caption"),
            "moved_to_chapter": ch_new.get("mapping_id"), **common}
        ch_new["tables_diff"][k_new] = {
            "kind": "moved_in", "caption": ch_new["tables_diff"][k_new].get("caption"),
            "moved_from_chapter": ch_old.get("mapping_id"), **common}
        moves.append({"from_chapter": ch_old.get("mapping_id"),
                      "to_chapter": ch_new.get("mapping_id"), **common})
    return moves


def tables_diff(old_sec_list, new_sec_list, sim_backend) -> list[dict]:
    ot = [t for s in old_sec_list for t in s.get("tables", []) if not _is_fragment_table(t)]
    nt = [t for s in new_sec_list for t in s.get("tables", []) if not _is_fragment_table(t)]
    matched = _assign_tables(ot, nt)
    out = []
    for j, t_new in enumerate(nt):
        if j not in matched:
            out.append({"kind": "new", "new": t_new["id"], "caption": t_new.get("caption")})
            continue
        i, score = matched[j]
        t_old = ot[i]
        changed_rows = _rows_changed(t_old, t_new)
        out.append({"kind": "matched", "old": t_old["id"], "new": t_new["id"],
                    "caption": t_new.get("caption") or t_old.get("caption"),
                    "rows_changed": changed_rows,
                    "identical": changed_rows == 0,
                    "confidence": round(score, 3)})
    used_o = {i for i, _ in matched.values()}
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


#: Imbalance from which a chapter mapping counts as implausible. A mapping pairing 89 old
#: paragraphs with 9 new ones (out/60909_2026-08) cannot be right: align_chapter pairs one
#: to one, so the old surplus is reported as "removed" regardless of the text.
UNBALANCED_MIN = 0.6
#: Below this many paragraphs on the larger side the imbalance carries no weight -- 1 vs. 0
#: is fully imbalanced and says nothing.
BALANCE_MIN_SIZE = 5


def paragraph_balance(old_sec_list: list[dict], new_sec_list: list[dict]) -> dict:
    """Paragraph mass of both sides of a chapter mapping -- a measurement, not a filter.

    Counts the same paragraphs the aligner sees (``align.paras._paras``: non-empty text,
    comparable kind), so ``old_surplus`` is exactly the number of old paragraphs that the
    one-to-one assignment cannot pair and therefore has to report as ``removed``.
    Nothing here changes the change stream; ``unbalanced`` only marks the mapping.
    """
    # local import: keeps this module free of the numpy import that align.paras carries
    from .align.paras import _paras

    n_old = sum(len(_paras(s)) for s in old_sec_list)
    n_new = sum(len(_paras(s)) for s in new_sec_list)
    larger = max(n_old, n_new)
    imbalance = round(abs(n_old - n_new) / larger, 4) if larger else 0.0
    return {"n_old": n_old, "n_new": n_new, "imbalance": imbalance,
            "old_surplus": max(0, n_old - n_new),
            "unbalanced": imbalance >= UNBALANCED_MIN and larger >= BALANCE_MIN_SIZE}


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
        old_sec_list = [o_secs[i] for i in old_ids if i in o_secs]
        new_sec_list = [n_secs[i] for i in new_ids if i in n_secs]
        balance = paragraph_balance(old_sec_list, new_sec_list)
        ch = {"mapping_id": rec.get("mapping_id") or mapping_id(old_ids, new_ids),
              "old_id": rec.get("old_id"), "new_id": rec.get("new_id"),
              "old_ids": old_ids, "new_ids": new_ids,
              "title": rec.get("new_title") or rec.get("old_title"),
              "level": rec.get("new_level") or rec.get("old_level"),
              "part": rec.get("new_part") or rec.get("old_part"),
              "part_changed": rec.get("part_changed", False),
              "mode": mt, "map_confidence": rec.get("confidence"),
              "paragraph_balance": balance,
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

        if balance["unbalanced"]:
            # marking only: the deletion stays in the stream, it just says where it comes from
            for c in ch["changes"]:
                if c["kind"] == "removed":
                    c["from_unbalanced_mapping"] = True

        ch["formulas_diff"] = formulas_diff(old_sec_list, new_sec_list)
        ch["tables_diff"] = tables_diff(old_sec_list, new_sec_list, sim_backend)
        chapters.append(ch)

    # AP-23: tables that changed chapters -- a move, not a deletion plus an addition
    cross_chapter_tables(chapters,
                         {t["id"]: t for s in old_doc["sections"] for t in s.get("tables", [])},
                         {t["id"]: t for s in new_doc["sections"] for t in s.get("tables", [])})

    # AP-11: where a removed text stands in the new edition, and whether that section was
    # in reach of the aligner. A marking on the record, nothing is filtered by it.
    annotate_relocation(chapters, new_doc)

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
