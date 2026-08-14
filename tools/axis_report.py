"""axis_report.py -- the four-axis taxonomy of a finished run, cross-tabulated (AP-14).

Reads the ``deutung.json`` of an output directory and reports what ENT-01 was
introduced for. Read-only, no LLM, no network: every value it needs is already on disk,
and not a sentence of standard text is interpreted here.

Six sections, in this order:

1. the cross table ``semantic_status`` x ``normative_direction`` -- the same shape as
   the finding it answers (``semantic_label`` x ``obligation``), so both can be laid
   side by side;
2. the share of self-contradictory combinations, against the share the old schema
   produces **on the same run**. Both rule sets are constants of this module
   (:data:`CONTRADICTIONS`, :data:`LEGACY_CONTRADICTIONS`), not a shape of the output:
   the share is the success measure of the package and has to be readable in one place;
3. how ``narrowed`` spreads over axis C -- ``restricted`` split 14 / 15 / 13 over the
   three directions, which is the defect. An even spread here means it was not fixed;
4. the migration table ``semantic_label`` (old) x ``semantic_status`` (new), the basis
   for deciding the later replacement of the old field;
5. axis D: frequency per component, distribution of the list length, and **every**
   ``other:`` value with its chapter -- that is how the vocabulary gets corrected;
6. abstention (ENT-02): frequency per reason code and how much of it falls on changes
   the old schema called ``restricted``.

Usage::

    poetry run python tools/axis_report.py --dir out/4110_2026-08b
    poetry run python tools/axis_report.py --dir out/4110_2026-08b --out runs/AP-14_2026-08-14
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.stages.deutung import (
    AFFECTED_COMPONENTS,
    INDETERMINATE,
    INDETERMINATE_REASONS,
    NORMATIVE_DIRECTIONS,
    OTHER_COMPONENT,
    SEMANTIC_LABELS,
    SEMANTIC_STATUS,
)

#: The axis fields of one interpretation, as they are read from ``deutung.json``.
AXES = ("structural_operation", "semantic_status", "normative_direction",
        "affected_components", "indeterminate_reason")

#: How an empty axis is printed. Not a value of any vocabulary -- it means the model
#: said nothing usable and the pipeline discarded what it did say.
EMPTY = "(none)"


def _components(row: dict) -> list:
    return row.get("affected_components") or []


#: Combinations that cannot both be true of the same change. Kept as a constant, and
#: deliberately narrow: every rule names a pair whose two halves contradict each other
#: by definition, not a pair that is merely rare.
CONTRADICTIONS = (
    # the statement is unchanged, yet the duty is said to move
    ("equivalent_but_directed",
     lambda r: r.get("semantic_status") == "equivalent"
     and r.get("normative_direction") in ("tightened", "relaxed")),
    # non-normative text that nevertheless touches a normative component
    ("not_applicable_with_component",
     lambda r: r.get("normative_direction") == "not_applicable"
     and any(c != "none" for c in _components(r))),
    # nothing changed about the statement, yet a proof obligation is affected
    ("equivalent_with_proof_obligation",
     lambda r: r.get("semantic_status") == "equivalent"
     and "proof_obligation" in _components(r)),
)

#: The same measurement for the old, flat schema -- recomputed on the same run rather
#: than quoted, so the comparison holds on any corpus. ``restricted`` counts as
#: contradictory in itself: it splits evenly over the three directions and therefore
#: cannot mean any one of them.
LEGACY_CONTRADICTIONS = (
    ("restricted_ambiguous", lambda r: r.get("semantic_label") == "restricted"),
    ("obligation_change_but_unchanged",
     lambda r: r.get("semantic_label") in ("new_obligation", "removed_obligation")
     and r.get("obligation") == "unchanged"),
)


def contradictions(row: dict, rules=CONTRADICTIONS) -> list[str]:
    """The names of the rules a row violates, in rule order (empty: none)."""
    return [name for name, rule in rules if rule(row)]


def collect(out_dir) -> list[dict]:
    """One row per interpretation, in the order ``deutung.json`` lists them."""
    path = Path(out_dir) / "deutung.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing -- the report needs the deutung.json of a "
                         "finished run.")
    deutung = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for c in deutung.get("chapters") or []:
        for d in (c.get("interpretations") or c.get("deutungen") or []):
            row = {"section_id": c.get("section_id"), "mapping_id": c.get("mapping_id"),
                   "change_index": d.get("change_index"),
                   "semantic_label": d.get("semantic_label"),
                   "obligation": d.get("obligation")}
            row.update({a: d.get(a) for a in AXES})
            row["contradictions"] = contradictions(row)
            row["legacy_contradictions"] = contradictions(row, LEGACY_CONTRADICTIONS)
            rows.append(row)
    return rows


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def summarize(rows: list[dict]) -> dict:
    """The six sections as numbers; :func:`render` only formats them."""
    n = len(rows)
    bad = [r for r in rows if r["contradictions"]]
    legacy_bad = [r for r in rows if r["legacy_contradictions"]]
    abstained = [r for r in rows
                 if INDETERMINATE in (r["semantic_status"], r["normative_direction"])]
    return {
        "interpretations": n,
        "with_all_axes": sum(1 for r in rows if all(r[a] is not None for a in AXES[:4])),
        "cross": Counter((r["semantic_status"], r["normative_direction"]) for r in rows),
        "contradictory": len(bad),
        "contradictory_pct": _pct(len(bad), n),
        "by_rule": Counter(name for r in bad for name in r["contradictions"]),
        "legacy_contradictory": len(legacy_bad),
        "legacy_contradictory_pct": _pct(len(legacy_bad), n),
        "legacy_by_rule": Counter(name for r in legacy_bad
                                  for name in r["legacy_contradictions"]),
        "narrowed": Counter(r["normative_direction"] for r in rows
                            if r["semantic_status"] == "narrowed"),
        "migration": Counter((r["semantic_label"], r["semantic_status"]) for r in rows),
        "structural": Counter(r["structural_operation"] for r in rows),
        # the third question of the package: does the old ``moved`` belong on axis A?
        "moved_operations": Counter(r["structural_operation"] for r in rows
                                    if r["semantic_label"] == "moved"),
        "components": Counter(c for r in rows for c in _components(r)),
        "component_list_lengths": Counter(len(_components(r)) for r in rows),
        "other_components": [{"component": c, "section_id": r["section_id"],
                              "change_index": r["change_index"]}
                             for r in rows for c in _components(r)
                             if isinstance(c, str) and c.startswith(OTHER_COMPONENT)],
        "indeterminate": len(abstained),
        "indeterminate_pct": _pct(len(abstained), n),
        "indeterminate_reasons": Counter(r["indeterminate_reason"] for r in abstained
                                         if r["indeterminate_reason"]),
        "indeterminate_on_restricted": sum(1 for r in abstained
                                           if r["semantic_label"] == "restricted"),
    }


def _table(counts: Counter, rows: list, columns: list, corner: str) -> list[str]:
    """A cross table with fixed row and column order -- empty rows are dropped."""
    width = max([len(corner)] + [len(str(r)) for r in rows])
    head = f"  {corner:<{width}}" + "".join(f"{c:>16}" for c in columns) + f"{'sum':>8}"
    out = [head, "  " + "-" * (len(head) - 2)]
    for r in rows:
        total = sum(counts[(r, c)] for c in columns)
        if not total:
            continue
        out.append(f"  {r!s:<{width}}"
                   + "".join(f"{counts[(r, c)] or '':>16}" for c in columns)
                   + f"{total:>8}")
    return out


def _values(seen: Counter, vocabulary: list[str]) -> list:
    """Vocabulary order first, then whatever else the run contains, then the empty axis."""
    known = [v for v in vocabulary if any(v == s for s in seen)]
    extra = sorted(str(s) for s in seen if s is not None and s not in vocabulary)
    return known + extra + ([None] if None in seen else [])


def _axis_values(counts: Counter, index: int, vocabulary: list[str]) -> list:
    return _values(Counter(k[index] for k in counts), vocabulary)


def render(rows: list[dict], summary: dict, out_dir, digests: dict) -> str:
    """The human-readable report (``axis_<run>.txt``)."""
    s = summary
    statuses = _axis_values(s["cross"], 0, SEMANTIC_STATUS)
    directions = _axis_values(s["cross"], 1, NORMATIVE_DIRECTIONS)
    cross = Counter({(EMPTY if k[0] is None else k[0],
                      EMPTY if k[1] is None else k[1]): v for k, v in s["cross"].items()})
    labels = _values(Counter(r["semantic_label"] for r in rows), SEMANTIC_LABELS)
    migration = Counter({(EMPTY if k[0] is None else k[0],
                          EMPTY if k[1] is None else k[1]): v
                         for k, v in s["migration"].items()})

    def name(v) -> str:
        return EMPTY if v is None else str(v)

    L = [f"axis report -- {out_dir}",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {n}: sha256 {sha}" for n, sha in sorted(digests.items())]
    L += [
        "",
        f"interpretations                      {s['interpretations']}",
        (f"  carrying all four axes             {s['with_all_axes']} "
         f"= {_pct(s['with_all_axes'], s['interpretations'])} %"),
        "",
        "1. semantic_status (B) x normative_direction (C)",
    ]
    L += _table(cross, [name(v) for v in statuses], [name(v) for v in directions],
                "B \\ C")
    L += [
        "",
        "2. self-contradictory combinations",
        (f"  four axes    {s['contradictory']:>6} / {s['interpretations']} "
         f"= {s['contradictory_pct']} %"),
    ]
    L += [f"      {n:<34}{c:>6}" for n, c in
          [(name, s["by_rule"][name]) for name, _ in CONTRADICTIONS]]
    L += [(f"  old schema   {s['legacy_contradictory']:>6} / {s['interpretations']} "
           f"= {s['legacy_contradictory_pct']} %   (the ~7 % of the 4110 run, "
           "recomputed here)")]
    L += [f"      {n:<34}{c:>6}" for n, c in
          [(name, s["legacy_by_rule"][name]) for name, _ in LEGACY_CONTRADICTIONS]]
    L += [
        "",
        "3. narrowed over axis C  (the defect: restricted split 14 / 15 / 13)",
    ]
    narrowed_total = sum(s["narrowed"].values())
    L += [f"      {name(d):<20}{s['narrowed'][d]:>6} = {_pct(s['narrowed'][d], narrowed_total)} %"
          for d in _values(s["narrowed"], NORMATIVE_DIRECTIONS)] or ["      (no narrowed)"]
    L += [
        "",
        "4. migration: semantic_label (old) x semantic_status (new)",
    ]
    L += _table(migration, [name(v) for v in labels], [name(v) for v in statuses],
                "old \\ new")
    L += ["", "  structural_operation (A) of the old label 'moved':"]
    L += [f"      {name(o):<20}{c:>6}" for o, c in sorted(
        s["moved_operations"].items(), key=lambda kv: (-kv[1], name(kv[0])))] \
        or ["      (no 'moved')"]
    L += [
        "",
        "5. affected_components (D)",
    ]
    L += [f"      {c:<28}{s['components'][c]:>6}"
          for c in AFFECTED_COMPONENTS if s["components"][c]]
    other_total = sum(v for k, v in s["components"].items()
                      if isinstance(k, str) and k.startswith(OTHER_COMPONENT))
    L += [f"      {'other:*':<28}{other_total:>6}"]
    L += ["", "  list length:"]
    L += [f"      {n:<28}{c:>6}"
          for n, c in sorted(s["component_list_lengths"].items())]
    L += ["", f"  every other: value ({len(s['other_components'])}), with its chapter:"]
    L += [f"      {o['section_id']!s:<22}{o['change_index']!s:>6}  {o['component']}"
          for o in s["other_components"]] or ["      (none)"]
    L += [
        "",
        "6. abstention (ENT-02)",
        (f"      {'interpretations':<28}{s['indeterminate']:>6} "
         f"= {s['indeterminate_pct']} %"),
    ]
    L += [f"      {r:<28}{s['indeterminate_reasons'][r]:>6}"
          for r in _values(s["indeterminate_reasons"], INDETERMINATE_REASONS)]
    L += [(f"      {'of those old restricted':<28}"
           f"{s['indeterminate_on_restricted']:>6}")]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="run output directory to read")
    ap.add_argument("--out", default=None, help="directory to write the report to "
                                                "(default: print only)")
    ap.add_argument("--name", default=None, help="report name (default: directory name)")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    rows = collect(src)
    summary = summarize(rows)
    digests = {"deutung.json": regression.sha256_file(src / "deutung.json")}
    text = render(rows, summary, src, digests)
    print(text)

    if args.out:
        dest = regression.guard_write(Path(args.out))
        dest.mkdir(parents=True, exist_ok=True)
        path = regression.guard_write(dest / f"axis_{args.name or src.name}.txt")
        path.write_text(text, encoding="utf-8")
        print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
