"""table_pairing_report.py -- why tables without a counterpart do not pair (AP-35).

Most tables reported without a counterpart carry no usable caption: 38 of 40 removed ones
at 4110, 21 of 23 at 4120. In an unpaired table every changed value is invisible, as much
to the deterministic diff as to the model, and that is the largest remaining blind spot of
the comparison.

The caption is not the cause. :func:`~normpare.stages.diff.table_similarity` has measured
**both** signals since AP-22 and takes the stronger one, so a captionless table is paired
over its content long since -- over ``_content_key``, the first three rows clipped to 400
characters (AP-23). The question is not *whether* content pairs, but why it does not catch
these tables. Three explanations are possible and they lead to different packages:

1. **chapter boundary** -- ``tables_diff`` runs per mapping record, so a candidate in
   another record is only reachable through ``cross_chapter_tables``, where
   :data:`~normpare.stages.diff.CROSS_CHAPTER_MIN` (0.80) applies instead of 0.55;
2. **displacement** -- the Hungarian assignment gives every new table away once, so a
   table can have lost its partner to a competitor inside its own record;
3. **no partner** -- nothing on the other side reaches
   :data:`~normpare.stages.diff.TABLE_MATCH_MIN`, and "without a counterpart" is right.

Which of the three prevails is unknown, and finding that out is the whole purpose. This
tool therefore **measures and changes nothing**: no threshold, no pairing rule, not a line
of production code. A wrongly paired table produces wrong value changes -- the most
expensive kind of error in a synopsis -- so it is measured first and decided afterwards.

Everything is computed offline from ``alt/norm_doc.json``, ``neu/norm_doc.json`` and
``synopse.json`` of a finished run, with the **production** functions
``table_similarity``, ``_content_key``, ``caption_text`` and ``_is_fragment_table``: a
rebuild would measure the tool instead of the pipeline. No LLM, no network, and the only
thing written goes to ``--out``.

Four sections per run:

1. **the base set** -- tables per side, paired (``matched``, ``moved_in``/``moved_away``),
   ``new``/``removed``, how many of those carry no usable caption and how many are
   fragments (a fragment is not a table and does not belong in the base set);
2. **the distribution** -- for every unpaired table the best hit among all unpaired
   tables of the other side, as a histogram in steps of 0.05, split by with and without
   caption. A threshold may only sit where the distribution has a gap;
3. **the three explanations**, counted against each other;
4. **the twenty strongest candidate pairs**, as material for the expert judgement --
   whether two tables are the same one is decided by a human at the standard text.

Below them the counter-calculation to the content window (narrow as in production against
the whole table) and how many of the unpaired tables carry parameter values at all.

``--dir`` may be given several times (the rule from CLAUDE.md): a finding on one corpus is
unconfirmed. Every run keeps its own report and a comparison view is added below them, one
column per run, in the order of the command line.

Usage::

    poetry run python tools/table_pairing_report.py --dir arbeit/4110_v3
    poetry run python tools/table_pairing_report.py --dir arbeit/4110_v3 \\
        --dir arbeit/4120_v3 --dir arbeit/60909_v2 --out runs/AP-35_2026-09-01
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.stages import diff
from normpare.stages.diff import (
    CROSS_CHAPTER_MIN,
    TABLE_MATCH_MIN,
    _assign_tables,
    _is_fragment_table,
    caption_text,
    table_similarity,
)
from normpare.stages.enrich import values

#: Width of a histogram class. Fixed, so the classes of two runs can be read side by side.
BIN_STEP = 0.05
#: The classes themselves, always all of them -- an empty class is a statement too.
BIN_LABELS = [f"{i * BIN_STEP:.2f}-{(i + 1) * BIN_STEP:.2f}" for i in range(20)]
#: How many candidate pairs section 4 lists per run.
TOP_PAIRS = 20
#: The wide window of the counter-calculation: the whole table instead of its head. Only
#: ever applied inside :func:`_window`, which puts the production values back.
WIDE_ROWS = 10_000
WIDE_CHARS = 4_000
#: How the three explanations are named and in which order they are reported.
EXPLANATIONS = ("displacement", "chapter_boundary", "no_candidate")


@contextmanager
def _window(rows: int, chars: int):
    """Run ``table_similarity`` with a different content window, then put production back.

    The counter-calculation to AP-23 has to compare the same measure at two widths, and
    the width sits in module constants that ``_content_key`` reads at call time. Patching
    them here keeps the *measure* the production one; the ``finally`` clause is what makes
    it a measurement instead of a change (``test_no_production_constant_is_touched``).
    """
    before = (diff.CONTENT_ROWS, diff.CONTENT_CHARS)
    diff.CONTENT_ROWS, diff.CONTENT_CHARS = rows, chars
    try:
        yield
    finally:
        diff.CONTENT_ROWS, diff.CONTENT_CHARS = before


def histogram(scores) -> list[int]:
    """Counts per class of 0.05, twenty classes, the upper end closed."""
    bins = [0] * 20
    for score in scores:
        # rounded before the floor: 0.60 / 0.05 is 11.999999999999998 in binary
        bins[min(math.floor(round(score / BIN_STEP, 9)), 19)] += 1
    return bins


def _doc_tables(doc: dict) -> dict[str, tuple[str, dict]]:
    """``id -> (section id, table)`` for every table of a document, in document order."""
    return {t["id"]: (s["id"], t)
            for s in doc.get("sections") or [] for t in s.get("tables") or []}


def _full_assignment(ot: list[dict], nt: list[dict]) -> dict[int, int]:
    """The assignment of one record **before** the threshold cuts the weak pairs out.

    :func:`~normpare.stages.diff._assign_tables` returns only the pairs at or above
    :data:`TABLE_MATCH_MIN`; which partner a table lost out to is exactly what falls away
    there. This runs the same optimum with the same ``_lsa`` and the same
    ``table_similarity`` and keeps every pair, so displacement can be named. It is checked
    against the production function afterwards (``reconstruction_mismatches``), so the two
    cannot drift apart.
    """
    if not ot or not nt:
        return {}
    from normpare.stages.align.paras import _lsa

    sim = [[table_similarity(a, b) for b in nt] for a in ot]
    rows, cols = _lsa([[-s for s in row] for row in sim])
    return {int(j): int(i) for i, j in zip(rows, cols)}


def _records(old_doc: dict, new_doc: dict, synopse: dict) -> list[dict]:
    """One entry per mapping record: its two table lists, its assignment, its verdict.

    The table lists are rebuilt the way ``tables_diff`` builds them -- the sections of
    ``old_ids``/``new_ids`` in their order, fragments dropped -- and the rebuild is held
    against what the run actually recorded.
    """
    o_secs = {s["id"]: s for s in old_doc.get("sections") or []}
    n_secs = {s["id"]: s for s in new_doc.get("sections") or []}
    out = []
    for ch in synopse.get("chapters") or []:
        ot = [t for i in ch.get("old_ids") or [] if i in o_secs
              for t in o_secs[i].get("tables") or [] if not _is_fragment_table(t)]
        nt = [t for i in ch.get("new_ids") or [] if i in n_secs
              for t in n_secs[i].get("tables") or [] if not _is_fragment_table(t)]
        matched = _assign_tables(ot, nt)
        assignment = _full_assignment(ot, nt)
        # what the run recorded, against what the rebuild produces
        recorded = {(r["old"], r["new"]) for r in ch.get("tables_diff") or []
                    if r["kind"] == "matched"}
        rebuilt = {(ot[i]["id"], nt[j]["id"]) for j, (i, _s) in matched.items()}
        out.append({"mapping_id": ch.get("mapping_id"), "title": ch.get("title"),
                    "old": ot, "new": nt, "matched": matched, "assignment": assignment,
                    "by_old": {i: j for j, i in assignment.items()},
                    "mismatch": len(recorded ^ rebuilt),
                    "tables_diff": ch.get("tables_diff") or []})
    return out


def _paired(records: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]],
                                          dict[str, int]]:
    """The ids the run considers paired, per side and per way, plus the counts per kind.

    ``matched`` and ``moved`` are kept apart because they are two different statements:
    the first is a pair inside one mapping record, the second one the document-wide pass
    of AP-23 found across chapter boundaries.
    """
    old: dict[str, set[str]] = {"matched": set(), "moved": set()}
    new: dict[str, set[str]] = {"matched": set(), "moved": set()}
    kinds: dict[str, int] = {k: 0 for k in
                             ("matched", "moved_away", "moved_in", "removed", "new")}
    for rec in records:
        for r in rec["tables_diff"]:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            if r["kind"] not in ("matched", "moved_away", "moved_in"):
                continue
            way = "matched" if r["kind"] == "matched" else "moved"
            if r.get("old"):
                old[way].add(r["old"])
            if r.get("new"):
                new[way].add(r["new"])
    return old, new, kinds


def _cell_values(table: dict) -> int:
    """Parameter values in the cells -- read where the enrichment wrote them (AP-31),
    computed with the same production function for a run from before it."""
    recs = table.get("cell_values")
    if recs is None:
        recs = values.cell_values(table.get("cells"))
    return len(recs)


def _side_inventory(doc_tables, in_records, paired: dict[str, set[str]], unpaired) -> dict:
    fragments = sum(1 for _sid, t in doc_tables.values() if _is_fragment_table(t))
    captionless = sum(1 for tid in unpaired
                      if caption_text(doc_tables[tid][1].get("caption")) is None)
    return {"tables": len(doc_tables), "fragments": fragments,
            "in_records": len(in_records),
            "matched": len(paired["matched"]), "moved": len(paired["moved"]),
            "paired": len(paired["matched"] | paired["moved"]),
            "unpaired": len(unpaired), "unpaired_captionless": captionless}


def _brief(doc_tables, tid: str, score: float | None = None) -> dict:
    section, table = doc_tables[tid]
    out = {"table": tid, "section": section,
           "caption": caption_text(table.get("caption")),
           "n_rows": table.get("n_rows"), "n_cols": table.get("n_cols")}
    if score is not None:
        out["score"] = score
    return out


def analyse(old_doc: dict, new_doc: dict, synopse: dict, src, label: str) -> dict:
    """The four sections of the report for one finished run."""
    old_tables, new_tables = _doc_tables(old_doc), _doc_tables(new_doc)
    records = _records(old_doc, new_doc, synopse)
    old_ok, new_ok, kinds = _paired(records)

    in_old = {t["id"] for rec in records for t in rec["old"]}
    in_new = {t["id"] for rec in records for t in rec["new"]}
    free_old = sorted(in_old - old_ok["matched"] - old_ok["moved"])
    free_new = sorted(in_new - new_ok["matched"] - new_ok["moved"])

    # which records a table belongs to -- the same table may sit in more than one
    records_of: dict[str, list[int]] = {}
    for k, rec in enumerate(records):
        for t in rec["old"] + rec["new"]:
            records_of.setdefault(t["id"], []).append(k)

    rows = [_analyse_table(tid, "old", records, records_of, old_tables, new_tables,
                           free_new) for tid in free_old]
    rows += [_analyse_table(tid, "new", records, records_of, new_tables, old_tables,
                            free_old) for tid in free_new]

    scores = {"all": [], "captioned": [], "captionless": []}
    for r in rows:
        if r["best_unpaired"]:
            scores["all"].append(r["best_unpaired"]["score"])
            scores["captioned" if r["captioned"] else "captionless"].append(
                r["best_unpaired"]["score"])
    explanations = {k: sum(1 for r in rows if r["explanation"] == k) for k in EXPLANATIONS}
    by_caption = {group: {k: sum(1 for r in rows if r["explanation"] == k
                                 and r["captioned"] == (group == "captioned"))
                          for k in EXPLANATIONS}
                  for group in ("captioned", "captionless")}
    cross = {"below_min": sum(1 for r in rows if r["below_cross_chapter_min"] is True),
             "at_or_above_min": sum(1 for r in rows
                                    if r["below_cross_chapter_min"] is False)}

    return {"dir": Path(src), "label": label,
            "inventory": {
                "old": _side_inventory(old_tables, in_old, old_ok, free_old),
                "new": _side_inventory(new_tables, in_new, new_ok, free_new),
                "kinds": kinds, "records": len(records),
                "reconstruction_mismatches": sum(rec["mismatch"] for rec in records)},
            "rows": rows,
            "histogram": {k: histogram(v) for k, v in scores.items()},
            "median": {k: (round(statistics.median(v), 4) if v else None)
                       for k, v in scores.items()},
            "explanations": explanations, "explanations_by_caption": by_caption,
            "cross_chapter": cross,
            "top_pairs": _top_pairs(rows, old_tables, new_tables),
            "window": _window_check(old_tables, new_tables),
            "values": _value_census(rows, old_tables, new_tables),
            "digests": {name: regression.sha256_file(Path(src) / name)
                        for name in ("synopse.json",)}}


def _analyse_table(tid: str, side: str, records, records_of, own, other,
                   free_other: list[str]) -> dict:
    """One unpaired table: its best candidates, and which of the three explanations fits.

    Two candidates are looked for, because the two mechanisms are different ones. Inside
    its own record **every** table of the other side counts, paired or not -- that is
    where the assignment separated them. Outside it only the unpaired ones count, because
    ``cross_chapter_tables`` never sees a table that its own chapter has already paired.
    """
    section, table = own[tid]
    best_free = None
    for other_id in free_other:
        score = table_similarity(*((table, other[other_id][1]) if side == "old"
                                   else (other[other_id][1], table)))
        if best_free is None or score > best_free["score"]:
            best_free = _brief(other, other_id, score)

    best_record, competitor, assigned = None, None, None
    for k in records_of.get(tid, []):
        rec = records[k]
        mine, theirs = (rec["old"], rec["new"]) if side == "old" else (rec["new"], rec["old"])
        index = next(i for i, t in enumerate(mine) if t["id"] == tid)
        # the record assignment is kept as new index -> old index; from this side's view
        # "mine" and "theirs" swap, so both directions are needed
        to_theirs = rec["by_old"] if side == "old" else rec["assignment"]
        to_mine = rec["assignment"] if side == "old" else rec["by_old"]
        for j, partner in enumerate(theirs):
            score = table_similarity(*((table, partner) if side == "old"
                                       else (partner, table)))
            if best_record is None or score > best_record["score"]:
                best_record = _brief(other, partner["id"], score)
                # who the assignment gave this candidate to, and what this table got
                taken_by, got = to_mine.get(j), to_theirs.get(index)
                competitor = mine[taken_by]["id"] if taken_by is not None else None
                assigned = theirs[got]["id"] if got is not None else None

    if best_record and best_record["score"] >= TABLE_MATCH_MIN:
        explanation, candidate = "displacement", best_record
        below = None
    elif best_free and best_free["score"] >= TABLE_MATCH_MIN:
        explanation, candidate = "chapter_boundary", best_free
        below = best_free["score"] < CROSS_CHAPTER_MIN
    else:
        explanation, candidate, below = "no_candidate", None, None

    return {"side": side, "table": tid, "section": section,
            "caption": caption_text(table.get("caption")),
            "captioned": caption_text(table.get("caption")) is not None,
            "n_rows": table.get("n_rows"), "n_cols": table.get("n_cols"),
            "n_cell_values": _cell_values(table),
            "mapping_ids": [records[k]["mapping_id"] for k in records_of.get(tid, [])],
            "best_unpaired": best_free, "best_in_record": best_record,
            "candidate": candidate, "competitor": competitor if competitor != tid else None,
            "assigned_to": assigned, "explanation": explanation,
            "below_cross_chapter_min": below}


def _top_pairs(rows: list[dict], old_tables, new_tables) -> list[dict]:
    """The strongest candidate pairs of the run, each named only once."""
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        cand = r["candidate"] or r["best_unpaired"]
        if not cand:
            continue
        key = (r["table"], cand["table"]) if r["side"] == "old" else (cand["table"], r["table"])
        if key not in seen or cand["score"] > seen[key]["score"]:
            seen[key] = {"old": key[0], "new": key[1], "score": cand["score"],
                         "explanation": r["explanation"]}
    pairs = sorted(seen.values(), key=lambda p: (-p["score"], p["old"], p["new"]))
    out = []
    for p in pairs[:TOP_PAIRS]:
        o_sec, o_tab = old_tables[p["old"]]
        n_sec, n_tab = new_tables[p["new"]]
        out.append({**p,
                    "old_section": o_sec, "new_section": n_sec,
                    "old_caption": caption_text(o_tab.get("caption")),
                    "new_caption": caption_text(n_tab.get("caption")),
                    "old_size": (o_tab.get("n_rows"), o_tab.get("n_cols")),
                    "new_size": (n_tab.get("n_rows"), n_tab.get("n_cols")),
                    "old_head": [" | ".join(r) for r in (o_tab.get("cells") or [])[:2]],
                    "new_head": [" | ".join(r) for r in (n_tab.get("cells") or [])[:2]]})
    return out


def _window_check(old_tables, new_tables) -> dict:
    """The counter-calculation to AP-23: the head of a table against the whole of it.

    Measured over the captionless old tables, each against every new table of the
    document -- the same quantity the package was handed as an order of magnitude. If the
    numbers disagree with it, this one counts: it is computed with ``table_similarity``.
    """
    olds = [t for _s, t in old_tables.values()
            if not _is_fragment_table(t) and caption_text(t.get("caption")) is None]
    news = [t for _s, t in new_tables.values() if not _is_fragment_table(t)]
    if not olds or not news:
        return {"n": len(olds), "narrow": None, "wide": None}

    def measure() -> dict:
        best = [max(table_similarity(o, n) for n in news) for o in olds]
        return {"median": round(statistics.median(best), 4),
                "at_least_min": sum(1 for s in best if s >= TABLE_MATCH_MIN)}

    narrow = measure()
    with _window(WIDE_ROWS, WIDE_CHARS):
        wide = measure()
    return {"n": len(olds), "narrow": narrow, "wide": wide}


def _value_census(rows: list[dict], old_tables, new_tables) -> dict:
    """How many unpaired tables carry parameter values at all -- one without a single
    value is no loss to the synopsis, and that either relaxes the blind spot or sharpens it."""
    out = {}
    for side in ("old", "new"):
        mine = [r for r in rows if r["side"] == side]
        out[side] = {"tables": len(mine),
                     "with_values": sum(1 for r in mine if r["n_cell_values"]),
                     "values": sum(r["n_cell_values"] for r in mine)}
    return out


def load(out_dir, label: str | None = None) -> dict:
    """One finished run: the two documents, the synopsis, and the numbers on them."""
    src = Path(out_dir)
    needed = ("synopse.json", "alt/norm_doc.json", "neu/norm_doc.json")
    for name in needed:
        if not (src / name).exists():
            raise SystemExit(f"{src / name} is missing -- the report needs the "
                             f"synopse.json and both norm_doc.json of a finished run.")
    return analyse(json.loads((src / "alt/norm_doc.json").read_text(encoding="utf-8")),
                   json.loads((src / "neu/norm_doc.json").read_text(encoding="utf-8")),
                   json.loads((src / "synopse.json").read_text(encoding="utf-8")),
                   src, label or src.name)


# --- rendering ---------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f} %" if whole else "    -  "


def _gap(bins: list[int]) -> str:
    """Where the distribution is empty between its lowest and its highest occupied class.

    A threshold may only sit where the distribution has a gap (AP-23, AP-26). This says
    where the gaps are; whether one of them carries a threshold is a judgement, not a
    number, and it is made in the report, not here.
    """
    filled = [i for i, n in enumerate(bins) if n]
    if len(filled) < 2:
        return "(too few values)"
    runs, start = [], None
    for i in range(filled[0], filled[-1] + 1):
        if not bins[i] and start is None:
            start = i
        elif bins[i] and start is not None:
            runs.append((start, i - 1))
            start = None
    if not runs:
        return "none -- the distribution is closed"
    return ", ".join(f"{BIN_LABELS[a].split('-')[0]}..{BIN_LABELS[b].split('-')[1]}"
                     f" ({b - a + 1} class{'es' if b > a else ''})" for a, b in runs)


def render(run: dict) -> str:
    inv, L = run["inventory"], []
    L.append(f"table pairing -- {run['label']}   ({run['dir']})")
    L.append("=" * 78)
    L.append("")
    L.append("1. the base set")
    L.append(f"   mapping records: {inv['records']}   "
             f"rebuild against the run: {inv['reconstruction_mismatches']} mismatches")
    L.append(f"   {'':<28}{'old':>10}{'new':>10}")
    for key, name in (("tables", "tables in the document"), ("fragments", "of them fragments"),
                      ("in_records", "in a mapping record"), ("matched", "paired: matched"),
                      ("moved", "paired: moved across chapters"), ("paired", "paired total"),
                      ("unpaired", "unpaired"),
                      ("unpaired_captionless", "  of them without caption")):
        L.append(f"   {name:<28}{inv['old'][key]:>10}{inv['new'][key]:>10}")
    L.append("   records in tables_diff: " +
             ", ".join(f"{k} {v}" for k, v in sorted(inv["kinds"].items())))
    L.append("")

    L.append("2. the distribution -- best hit among the unpaired of the other side")
    L.append(f"   {'class':<14}{'all':>8}{'captioned':>12}{'captionless':>14}")
    for i, label in enumerate(BIN_LABELS):
        L.append(f"   {label:<14}{run['histogram']['all'][i]:>8}"
                 f"{run['histogram']['captioned'][i]:>12}"
                 f"{run['histogram']['captionless'][i]:>14}")
    for key in ("all", "captioned", "captionless"):
        L.append(f"   median {key:<20}{run['median'][key]}")
        L.append(f"   gap    {key:<20}{_gap(run['histogram'][key])}")
    L.append(f"   (a threshold may only sit in a gap; TABLE_MATCH_MIN = {TABLE_MATCH_MIN}, "
             f"CROSS_CHAPTER_MIN = {CROSS_CHAPTER_MIN})")
    L.append("")

    total = sum(run["explanations"].values())
    L.append("3. the three explanations")
    for key in EXPLANATIONS:
        n = run["explanations"][key]
        L.append(f"   {key:<20}{n:>6}  {_pct(n, total)}   "
                 f"captioned {run['explanations_by_caption']['captioned'][key]:>4}, "
                 f"without {run['explanations_by_caption']['captionless'][key]:>4}")
    L.append(f"   {'total':<20}{total:>6}")
    L.append(f"   of the chapter-boundary cases below CROSS_CHAPTER_MIN: "
             f"{run['cross_chapter']['below_min']}, at or above: "
             f"{run['cross_chapter']['at_or_above_min']}")
    L.append("")

    L.append(f"4. the {TOP_PAIRS} strongest candidate pairs")
    if not run["top_pairs"]:
        L.append("   (none)")
    for p in run["top_pairs"]:
        L.append(f"   {p['score']:.3f}  {p['explanation']}")
        L.append(f"      old {p['old_section']:<12} {p['old']}  "
                 f"{p['old_size'][0]}x{p['old_size'][1]}  "
                 f"caption: {p['old_caption'] or '(none)'}")
        for line in p["old_head"]:
            L.append(f"         | {line[:96]}")
        L.append(f"      new {p['new_section']:<12} {p['new']}  "
                 f"{p['new_size'][0]}x{p['new_size'][1]}  "
                 f"caption: {p['new_caption'] or '(none)'}")
        for line in p["new_head"]:
            L.append(f"         | {line[:96]}")
    L.append("")

    w = run["window"]
    L.append("5. counter-calculation to the content window (captionless old tables against "
             "all new ones)")
    L.append(f"   n = {w['n']}")
    for key in ("narrow", "wide"):
        name = (f"{diff.CONTENT_ROWS} rows / {diff.CONTENT_CHARS} chars" if key == "narrow"
                else "whole table / 4000 chars")
        if w[key] is None:
            L.append(f"   {key:<8}{name:<28}(nothing to measure)")
        else:
            L.append(f"   {key:<8}{name:<28}median {w[key]['median']:<8}"
                     f">= {TABLE_MATCH_MIN}: {w[key]['at_least_min']}")
    L.append("")

    v = run["values"]
    L.append("6. do the unpaired tables carry values at all (cell_values, AP-31)")
    for side in ("old", "new"):
        L.append(f"   {side:<6}{v[side]['with_values']} of {v[side]['tables']} tables "
                 f"carry a value, {v[side]['values']} values in total")
    L.append("")
    return "\n".join(L) + "\n"


def render_comparison(runs: list[dict]) -> str:
    """The runs side by side, one column each, in the order of the command line."""
    width = max(14, max(len(r["label"]) for r in runs) + 2)

    def line(label: str, get) -> str:
        return f"  {label:<40}" + "".join(f"{get(r)!s:>{width}}" for r in runs)

    L = ["table pairing -- comparison", "=" * 78, "",
         f"  {'':<40}" + "".join(f"{r['label']:>{width}}" for r in runs), ""]
    L.append("1. the base set")
    for side in ("old", "new"):
        for key, name in (("tables", "tables"), ("fragments", "fragments"),
                          ("paired", "paired"), ("unpaired", "unpaired"),
                          ("unpaired_captionless", "unpaired without caption")):
            L.append(line(f"{side}: {name}", lambda r, s=side, k=key: r["inventory"][s][k]))
    L.append("")
    L.append("2. the distribution")
    for key in ("all", "captioned", "captionless"):
        L.append(line(f"median of the best hit ({key})",
                      lambda r, k=key: r["median"][k]))
    L.append(line(f"unpaired with a hit >= {TABLE_MATCH_MIN}",
                  lambda r: sum(r["histogram"]["all"][11:])))
    L.append("")
    L.append("3. the three explanations")
    for key in EXPLANATIONS:
        L.append(line(key, lambda r, k=key: r["explanations"][k]))
    L.append(line("of them below CROSS_CHAPTER_MIN",
                  lambda r: r["cross_chapter"]["below_min"]))
    L.append("")
    L.append("5. content window")
    L.append(line("captionless old tables (n)", lambda r: r["window"]["n"]))
    for key in ("narrow", "wide"):
        L.append(line(f"{key}: median",
                      lambda r, k=key: (r["window"][k] or {}).get("median")))
        L.append(line(f"{key}: >= {TABLE_MATCH_MIN}",
                      lambda r, k=key: (r["window"][k] or {}).get("at_least_min")))
    L.append("")
    L.append("6. values in the unpaired tables")
    for side in ("old", "new"):
        L.append(line(f"{side}: tables with a value",
                      lambda r, s=side: r["values"][s]["with_values"]))
        L.append(line(f"{side}: values", lambda r, s=side: r["values"][s]["values"]))
    L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, action="append",
                    help="run output directory to read (repeatable: the runs are then "
                         "compared column by column, in the order given)")
    ap.add_argument("--out", default=None, help="directory to write the report to "
                                                "(default: print only)")
    ap.add_argument("--name", default=None, action="append",
                    help="report name of a run (default: directory name)")
    args = ap.parse_args(argv)

    names = args.name or []
    runs = [load(d, names[i] if i < len(names) else None) for i, d in enumerate(args.dir)]

    reports, used = [], set()
    for run in runs:
        name = f"table_pairing_{run['label']}.txt"
        k = 2
        while name in used:
            name, k = f"table_pairing_{run['label']}_{k}.txt", k + 1
        used.add(name)
        reports.append((name, render(run)))
    if len(runs) > 1:
        reports.append(("table_pairing_comparison.txt", render_comparison(runs)))
    for _name, text in reports:
        print(text)

    if args.out:
        dest = regression.guard_write(Path(args.out))
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in reports:
            path = regression.guard_write(dest / name)
            path.write_text(text, encoding="utf-8")
            print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
