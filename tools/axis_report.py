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

``--dir`` may be given several times (AP-15). Every run keeps its own report, unchanged
down to the character, and a comparison view is added below them with one column per
run, in the order of the command line. A finding on one corpus is unconfirmed: the
series AP-09..AP-13 repeatedly showed that a number from 4110 was a property of 4110 and
not of the method, which is why an interpretation change is measured on a connection
rule (4110) **and** a classical calculation standard (60909) side by side. Runs from
before the axes exist are carried as a column with a note, never silently skipped.

Usage::

    poetry run python tools/axis_report.py --dir out/4110_2026-08b
    poetry run python tools/axis_report.py --dir out/4110_2026-08b --out runs/AP-14_2026-08-14
    poetry run python tools/axis_report.py --dir out/4110_neu --dir out/60909_neu
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

#: The three axes the model answers. Axis A is derived by the pipeline and is therefore
#: no evidence that a run knows the axes at all.
MODEL_AXES = ("semantic_status", "normative_direction", "affected_components")

#: How a run from before the axes is marked in a comparison column. It is carried with
#: this note rather than skipped: a missing column would make the comparison look
#: complete when it is not, and a default of ``0`` would be an invented measurement.
NO_AXES = "(no axes)"

#: The same for the old, flat schema. ``out/4110_haiku`` still carries German values
#: (``semantic_label: "erweitert"``); the legacy rules find nothing there and would
#: report a spotless 0.0 % that is the absence of the field, not a property of the run.
NO_LABELS = "(no old labels)"


def _components(row: dict) -> list:
    return row.get("affected_components") or []


#: Combinations that cannot both be true of the same change. Kept as a constant, and
#: deliberately narrow: every rule names a pair whose two halves contradict each other
#: by definition, not a pair that is merely rare.
#:
#: A third rule, ``not_applicable_with_component``, was dropped in AP-16. It flagged
#: non-normative text that touches a normative component and produced 569 of 589
#: reported contradictions on 4110 and 712 of 715 on 60909 -- every sampled case sound:
#: the title of a referenced standard changes (``reference``) without any duty moving.
#: Axis D says **what** a change is about, axis C whether a duty moves; the rule equated
#: the two and so measured the very category error ENT-01 removes.
CONTRADICTIONS = (
    # the statement is unchanged, yet the duty is said to move
    ("equivalent_but_directed",
     lambda r: r.get("semantic_status") == "equivalent"
     and r.get("normative_direction") in ("tightened", "relaxed")),
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


# -- several runs side by side (AP-15) ----------------------------------------------------

def load(out_dir, label: str | None = None) -> dict:
    """One run as the comparison needs it: rows, numbers, label and digest."""
    src = Path(out_dir)
    rows = collect(src)
    return {"dir": src, "label": label or src.name, "rows": rows,
            "summary": summarize(rows),
            "digest": regression.sha256_file(src / "deutung.json"),
            "has_axes": any(r[a] is not None for r in rows for a in MODEL_AXES),
            "has_labels": any(r["semantic_label"] in SEMANTIC_LABELS for r in rows)}


def _column_width(runs: list[dict]) -> int:
    """Wide enough for the longest run label and for ``1749 = 100.0 %``."""
    return max(18, max(len(r["label"]) for r in runs) + 2)


def _row(label: str, cells: list[str], indent: str = "  ") -> tuple:
    """A row of the comparison, formatted later: its label column has to fit them all."""
    return ("row", indent, label, [str(c) for c in cells])


def _format(lines: list, width: int) -> list[str]:
    """Turn the collected rows into text, with one label column for the whole report.

    The width has to be found after the fact: a free ``other:<label>`` is as long as
    whoever wrote it made it, and a column that fits every row is the difference between
    a table and a heap.
    """
    label_width = max([34] + [len(i[1]) + len(i[2]) + 2
                              for i in lines if isinstance(i, tuple)])
    out = []
    for item in lines:
        if not isinstance(item, tuple):
            out.append(item)
            continue
        _, indent, label, cells = item
        out.append(f"{indent}{label:<{label_width - len(indent)}}"
                   + "".join(f"{c:>{width}}" for c in cells))
    return out


def _count(n: int, whole: int) -> str:
    return f"{n} = {_pct(n, whole)} %"


def _per_run(runs: list[dict], value, axis_based: bool = True) -> list[str]:
    """One cell per run -- with a note where the run cannot carry the measurement.

    ``axis_based=False`` marks a row that reads the old, flat schema instead; both notes
    exist so that no column ever shows a number the run did not produce.
    """
    def cell(r: dict) -> str:
        if axis_based:
            return NO_AXES if not r["has_axes"] else str(value(r))
        return NO_LABELS if not r["has_labels"] else str(value(r))

    return [cell(r) for r in runs]


def render_comparison(runs: list[dict]) -> str:
    """The comparison view: one column per run, in the order they were given.

    Column order follows the command line and is never sorted -- the reader decides
    which run is the reference, and in a two-corpus comparison the first one usually is.
    """
    width = _column_width(runs)
    first = runs[0]

    def counts(select) -> list[str]:
        """``n = x %`` per run, the share taken of that run's interpretations."""
        return _per_run(runs, lambda r: _count(select(r), r["summary"]["interpretations"]))

    L = [f"axis comparison -- {len(runs)} runs",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {r['label']}: {r['dir']}, deutung.json sha256 {r['digest']}" for r in runs]
    L += ["", _row("", [r["label"] for r in runs]),
          _row("interpretations",
               [str(r["summary"]["interpretations"]) for r in runs]),
          _row("carrying all four axes",
               counts(lambda r: r["summary"]["with_all_axes"]))]

    # 1. the cross table per run, then the difference of the shares
    L += ["", "1. semantic_status (B) x normative_direction (C), per run"]
    for r in runs:
        L += ["", f"  [{r['label']}]"]
        if not r["has_axes"]:
            L += [f"    {NO_AXES} -- this run was interpreted before ENT-01"]
            continue
        cross = Counter({(EMPTY if k[0] is None else k[0],
                          EMPTY if k[1] is None else k[1]): v
                         for k, v in r["summary"]["cross"].items()})
        statuses = _axis_values(r["summary"]["cross"], 0, SEMANTIC_STATUS)
        directions = _axis_values(r["summary"]["cross"], 1, NORMATIVE_DIRECTIONS)
        L += _table(cross, [EMPTY if v is None else str(v) for v in statuses],
                    [EMPTY if v is None else str(v) for v in directions], "B \\ C")
    L += ["", ("  share of all interpretations in %, and the difference against "
               f"{first['label']}:")]
    L += [_row("", [r["label"] for r in runs]
               + [f"d {r['label']}" for r in runs[1:]])]
    for combination in _combinations(runs):
        shares = [_share(r, combination) for r in runs]
        cells = _per_run(runs, lambda r, c=combination: f"{_share(r, c)}")
        cells += [NO_AXES if not r["has_axes"] or not first["has_axes"]
                  else f"{round(s - shares[0], 2):+}"
                  for r, s in zip(runs[1:], shares[1:])]
        L += [_row(" / ".join(combination), cells)]

    # 2. the share of self-contradictory combinations, against the 7.2 % of the old schema
    L += ["", "2. self-contradictory combinations",
          _row("four axes", counts(lambda r: r["summary"]["contradictory"]))]
    L += [_row(name, _per_run(runs, lambda r, n=name: r["summary"]["by_rule"][n]),
               indent="      ") for name, _ in CONTRADICTIONS]
    L += [_row("old schema",
               _per_run(runs, lambda r: _count(r["summary"]["legacy_contradictory"],
                                               r["summary"]["interpretations"]),
                        axis_based=False))]
    L += [_row(name, _per_run(runs, lambda r, n=name: r["summary"]["legacy_by_rule"][n],
                              axis_based=False), indent="      ")
          for name, _ in LEGACY_CONTRADICTIONS]
    L += ["      (the old schema produced 7.2 % on out/4110_2026-08b: 126 of 1749)"]

    # 3. does narrowed spread over axis C the way restricted did (14 / 15 / 13)?
    L += ["", "3. narrowed over axis C  (the defect: restricted split 14 / 15 / 13)",
          _row("narrowed",
               _per_run(runs, lambda r: sum(r["summary"]["narrowed"].values())))]
    directions = _union(runs, lambda r: r["summary"]["narrowed"], NORMATIVE_DIRECTIONS)
    L += [_row(EMPTY if d is None else str(d),
               _per_run(runs, lambda r, d=d: _count(
                   r["summary"]["narrowed"][d], sum(r["summary"]["narrowed"].values()))),
               indent="      ")
          for d in directions] or ["      (no narrowed in any run)"]

    # 4. axis D side by side -- where a connection rule differs from a calculation standard
    L += ["", "4. affected_components (D), per run: count and share of interpretations"]
    # every value of the vocabulary, a zero included: whether the five values AP-16 added
    # are picked up at all is what the next run has to answer, and a row that disappears
    # at zero cannot be told from a value that was never offered. It also keeps the rows
    # of all runs lined up, which is the point of putting them side by side.
    for c in AFFECTED_COMPONENTS:
        L += [_row(c, counts(lambda r, c=c: r["summary"]["components"][c]),
                   indent="      ")]
    # the free values are summed up here and listed one by one in section 5
    L += [_row("other:*", counts(_other_total), indent="      ")]

    # 5. every other: value of every run, in one list, with the run it comes from
    L += ["", "5. other: values of all runs, pooled"]
    labels = sorted({o["component"] for r in runs for o in r["summary"]["other_components"]})
    L += [_row(label, _per_run(runs, lambda r, la=label: sum(
        1 for o in r["summary"]["other_components"] if o["component"] == la)),
        indent="      ") for label in labels] or ["      (none in any run)"]
    L += ["", "  every occurrence, with its run and chapter:"]
    L += [f"      {r['label']:<20}{o['section_id']!s:<16}{o['change_index']!s:>6}"
          f"  {o['component']}"
          for r in runs for o in r["summary"]["other_components"]] or ["      (none)"]

    # 6. abstention per reason code and run
    L += ["", "6. abstention (ENT-02)",
          _row("interpretations",
               counts(lambda r: r["summary"]["indeterminate"]))]
    L += [_row(str(reason),
               _per_run(runs, lambda r, x=reason:
                        r["summary"]["indeterminate_reasons"][x]), indent="      ")
          for reason in _union(runs, lambda r: r["summary"]["indeterminate_reasons"],
                               INDETERMINATE_REASONS)]
    L += [_row("of those old restricted",
               _per_run(runs, lambda r: r["summary"]["indeterminate_on_restricted"]),
               indent="      ")]
    return "\n".join(_format(L, width)) + "\n"


def _other_total(run: dict) -> int:
    return sum(v for k, v in run["summary"]["components"].items()
               if isinstance(k, str) and k.startswith(OTHER_COMPONENT))


def _union(runs: list[dict], select, vocabulary: list[str]) -> list:
    """The values any run uses, vocabulary order first -- so columns share their rows."""
    seen = Counter()
    for r in runs:
        seen.update({k: 1 for k in select(r)})
    return _values(seen, vocabulary)


def _combinations(runs: list[dict]) -> list[tuple]:
    """Every (B, C) pair occurring in any run, in vocabulary order."""
    seen = Counter()
    for r in runs:
        seen.update({k: 1 for k in r["summary"]["cross"]})
    statuses = _values(Counter(k[0] for k in seen), SEMANTIC_STATUS)
    directions = _values(Counter(k[1] for k in seen), NORMATIVE_DIRECTIONS)
    return [(EMPTY if s is None else str(s), EMPTY if d is None else str(d))
            for s in statuses for d in directions if (s, d) in seen]


def _share(run: dict, combination: tuple) -> float:
    """Share of the interpretations of one run falling on one (B, C) pair, in %."""
    s = run["summary"]
    total = sum(v for k, v in s["cross"].items()
                if (EMPTY if k[0] is None else str(k[0]),
                    EMPTY if k[1] is None else str(k[1])) == combination)
    return _pct(total, s["interpretations"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, action="append",
                    help="run output directory to read (repeatable: the runs are then "
                         "compared column by column, in the order given)")
    ap.add_argument("--out", default=None, help="directory to write the report to "
                                                "(default: print only)")
    ap.add_argument("--name", default=None, help="report name of a single run "
                                                 "(default: directory name)")
    args = ap.parse_args(argv)

    runs = [load(d) for d in args.dir]
    reports = [(f"axis_{args.name or r['dir'].name}.txt" if len(runs) == 1
                else f"axis_{r['label']}.txt",
                render(r["rows"], r["summary"], r["dir"],
                       {"deutung.json": r["digest"]}))
               for r in runs]
    if len(runs) > 1:
        reports.append(("axis_comparison.txt", render_comparison(runs)))
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
