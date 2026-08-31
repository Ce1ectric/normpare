"""axis_consistency.py -- the axes of a finished run against what the pipeline knows (AP-29).

Recomputes the four findings of AP-29 over one or more finished runs, offline: no LLM,
no network, not a sentence of standard text interpreted here. Everything it needs is on
disk in ``synopse.json`` (the facts) and ``deutung.json`` (the interpretation), and every
number comes from the **production** functions of :mod:`normpare.stages.deutung`, so the
report and the pipeline cannot drift apart.

Four sections, in the order of the findings:

1. **the move pairs** -- joined over ``moved_away.moved_to`` -> ``moved_in.new_ids[0]``,
   with how many were checked, how many carry no pointer, and how many pointers hit a
   record the comparison does not call ``moved_in`` (finding 1a). Then the disagreement
   between the two sides per axis, with the signature that produced it;
2. **axis B against axis A** -- ``equivalent`` for a change without a counterpart;
3. **axis C against the deterministic modality** -- a modal change called non-normative,
   and, counted separately and never written on a record, the softer reverse case;
4. **the free text against axis B** -- ``narrowed`` while the text names the successor,
   in the narrow form that runs and in the wide form that does not.

Below them the counter-calculation for the rule of finding 1: how many records the rule
"a move is equivalent unless the text changed" turns around, on each side.

``--dir`` may be given several times (the rule from CLAUDE.md): a finding on one corpus
is unconfirmed. Every run keeps its own report and a comparison view is added below them,
one column per run, in the order of the command line.

Usage::

    poetry run python tools/axis_consistency.py --dir arbeit/4110_v3
    poetry run python tools/axis_consistency.py --dir arbeit/4110_v3 --dir arbeit/60909_v2 \\
        --out runs/AP-29_2026-08-31
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
    CONSISTENCY_FLAGS,
    MODAL_LABELS,
    PARTNER_AXES,
    change_modality,
    check_consistency,
    move_pairs,
    successor_hint,
    successor_named,
)

#: How an empty axis is printed -- it is not a value of any vocabulary.
EMPTY = "(none)"


def _name(value) -> str:
    return EMPTY if value is None else str(value)


def load(out_dir, label: str | None = None) -> dict:
    """One run: the two artefacts, the flags written onto a copy, and the numbers.

    The interpretation is read into memory and marked there. Nothing is written back --
    the run directory of a finished run is evidence, and a tool that edits its evidence
    measures itself.
    """
    src = Path(out_dir)
    for name in ("synopse.json", "deutung.json"):
        if not (src / name).exists():
            raise SystemExit(f"{src / name} is missing -- the report needs the "
                             f"synopse.json and the deutung.json of a finished run.")
    synopse = json.loads((src / "synopse.json").read_text(encoding="utf-8"))
    deutung = json.loads((src / "deutung.json").read_text(encoding="utf-8"))
    chapters = deutung.get("chapters") or []
    report = check_consistency(chapters, synopse)
    return {"dir": src, "label": label or src.name, "synopse": synopse,
            "deutung": deutung, "report": report,
            "extra": details(chapters, synopse),
            "digests": {"synopse.json": regression.sha256_file(src / "synopse.json"),
                        "deutung.json": regression.sha256_file(src / "deutung.json")}}


def _rows(chapters: list[dict], synopse: dict) -> list[dict]:
    """One row per interpretation, with the change record it addresses."""
    changes = {ch.get("mapping_id"): (ch.get("changes") or [])
               for ch in (synopse.get("chapters") or [])}
    out = []
    for ch in chapters:
        mid = ch.get("mapping_id")
        records = changes.get(mid) or []
        for d in (ch.get("interpretations") or ch.get("deutungen") or []):
            i = d.get("change_index")
            change = records[i] if isinstance(i, int) and 0 <= i < len(records) else None
            out.append({"mapping_id": mid, "section_id": ch.get("section_id"),
                        "change_index": i, "deutung": d, "change": change})
    return out


def details(chapters: list[dict], synopse: dict) -> dict:
    """What the report shows beyond the counts of :func:`check_consistency`.

    The signatures of the disagreement, the two sides of the counter-calculation, the
    wide form of the successor recogniser and the overlap of the four flags -- none of
    them is a decision of the pipeline, so none of them belongs in the production
    report.
    """
    rows = _rows(chapters, synopse)
    by_key = {(r["mapping_id"], r["change_index"]): r for r in rows}
    joined = move_pairs(synopse)

    signatures = {a: Counter() for a in PARTNER_AXES}
    for pair in joined["pairs"]:
        away = by_key.get((pair["away"]["mapping_id"], pair["away"]["change_index"]))
        into = by_key.get((pair["into"]["mapping_id"], pair["into"]["change_index"]))
        if away is None or into is None:
            continue
        for axis in PARTNER_AXES:
            a, b = away["deutung"].get(axis), into["deutung"].get(axis)
            if a != b:
                signatures[axis][(_name(a), _name(b))] += 1

    # the counter-calculation of the rule: what "a move is equivalent unless the text
    # changed" turns around, and what the opposite decision would have turned around
    moved = {"moved_away": Counter(), "moved_in": Counter()}
    for r in rows:
        kind = (r["change"] or {}).get("kind")
        if kind in moved:
            moved[kind][_name(r["deutung"].get("semantic_status"))] += 1

    narrow = [r for r in rows if r["deutung"].get("semantic_status") == "narrowed"
              and successor_named(r["deutung"])]
    wide = [r for r in rows
            if r["deutung"].get("semantic_status") in ("narrowed", "extended")
            and successor_hint(r["deutung"])]
    return {
        "n_interpretations": len(rows),
        "n_missing_status": sum(1 for r in rows
                                if r["deutung"].get("semantic_status") is None),
        "signatures": signatures,
        "moved_away_status": moved["moved_away"], "moved_in_status": moved["moved_in"],
        "equivalent_on": Counter(
            r["deutung"].get("structural_operation") for r in rows
            if r["deutung"].get("semantic_status") == "equivalent"
            and r["deutung"].get("structural_operation") in ("added", "removed")),
        "modality_of_flagged": Counter(
            change_modality(r["change"]) for r in rows
            if r["deutung"].get("axis_c_contradicts_modality")),
        "narrow": narrow, "n_narrow": len(narrow), "n_wide": len(wide),
        "narrow_directions": Counter(_name(r["deutung"].get("normative_direction"))
                                     for r in narrow),
        "flag_counts": Counter(f for r in rows for f in CONSISTENCY_FLAGS
                               if r["deutung"].get(f)),
        "flags_per_row": Counter(sum(1 for f in CONSISTENCY_FLAGS if r["deutung"].get(f))
                                 for r in rows),
    }


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def _line(label: str, value, whole: int | None = None, indent: str = "      ") -> str:
    """One counted line, optionally with its share -- the shape of the whole report."""
    width = 40 - len(indent)
    text = f"{indent}{label:<{width}}{value:>8}"
    return text if whole is None else f"{text} = {_pct(value, whole)} %"


def render(run: dict) -> str:
    """The report of one run (``axis_consistency_<run>.txt``)."""
    r, x = run["report"], run["extra"]
    n_all = x["n_interpretations"]
    L = [f"axis consistency -- {run['dir']}",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {n}: sha256 {s}" for n, s in sorted(run["digests"].items())]
    L += ["",
          _line("interpretations", n_all, indent="  "),
          _line("without a semantic_status", x["n_missing_status"], indent="    "),
          "",
          "1. move pairs (finding 1)",
          _line("moved_away records",
                r["n_pairs"] + r["n_unpaired"] + r["n_dangling"]),
          _line("joined over the pointer", r["n_pairs"]),
          _line("both sides interpreted", r["n_pairs_interpreted"]),
          _line("no pointer, not checked", r["n_unpaired"]),
          _line("pointer into a non-move (1a)", r["n_dangling"]),
          _line("moved_in without its source (AP-30)", r.get("n_orphan", 0))]
    for d in r["dangling"]:
        L.append(f"        {d['mapping_id']} #{d['change_index']} -> {d['moved_to']} "
                 f"({', '.join(d['kinds']) or 'nothing'})")
    L += ["", "  the two sides disagree:"]
    for axis in PARTNER_AXES:
        L.append(_line(axis, r["by_axis"][axis], r["n_pairs_interpreted"]))
        for (a, b), n in x["signatures"][axis].most_common():
            L.append(f"        moved_away {a} / moved_in {b}: {n}")
    L += ["", "2. axis B against axis A (finding 2)",
          _line("equivalent without counterpart", r["n_axis_b_contradicts_a"], n_all)]
    L += [f"        on {op}: {n}" for op, n in sorted(x["equivalent_on"].items())]
    L += ["", "3. axis C against the modality (finding 3)",
          _line("modal, called not_applicable", r["n_axis_c_contradicts_modality"],
                n_all)]
    L += [f"        {m}: {n}" for m, n in sorted(x["modality_of_flagged"].items(),
                                                 key=lambda kv: (-kv[1], str(kv[0])))]
    L += [_line("informative, yet directed", r["n_informative_with_direction"], n_all),
          ("        (counted only -- no record is marked; the modality detection is "
           "not certain enough here)")]
    L += ["", "4. the free text against axis B (finding 4)",
          _line("narrowed with a named successor", x["n_narrow"])]
    L += [f"        also {d}: {n}" for d, n in x["narrow_directions"].most_common()]
    L += [_line("wide form (measured only)", x["n_wide"])]
    L += [f"        {r2['mapping_id']} #{r2['change_index']}: "
          f"{' '.join((r2['deutung'].get('impact') or r2['deutung'].get('change') or '').split())[:120]}"
          for r2 in x["narrow"]]
    L += ["", "5. the counter-calculation of the rule (finding 1)",
          "  the decision: a move is equivalent unless the text changed",
          _line("moved_away records interpreted", sum(x["moved_away_status"].values()))]
    L += [f"        {s}: {n}" for s, n in x["moved_away_status"].most_common()]
    L += [_line("moved_in records interpreted", sum(x["moved_in_status"].values()))]
    L += [f"        {s}: {n}" for s, n in x["moved_in_status"].most_common()]
    L += [_line("turned around by the rule", x["moved_away_status"]["replaced"]),
          _line("the opposite decision would turn", x["moved_in_status"]["equivalent"])]
    L += ["", "6. the four flags together",
          _line("interpretations with a flag", n_all - x["flags_per_row"][0])]
    L += [f"        {f}: {x['flag_counts'][f]}" for f in CONSISTENCY_FLAGS]
    L += [_line("more than one flag", r["n_multiple_flags"])]
    L += [f"        {n} flag(s): {c}" for n, c in sorted(x["flags_per_row"].items())
          if n]
    return "\n".join(L) + "\n"


def _row(label: str, cells: list, indent: str = "  ") -> tuple:
    return ("row", indent, label, [str(c) for c in cells])


def _format(lines: list, width: int) -> list[str]:
    label_width = max([36] + [len(i[1]) + len(i[2]) + 2
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


def render_comparison(runs: list[dict]) -> str:
    """One column per run, in the order of the command line -- the two-corpus rule.

    A number from one corpus is a property of that corpus until a second one confirms
    it; the series AP-09..AP-13 showed that repeatedly.
    """
    width = max(16, max(len(r["label"]) for r in runs) + 2)

    def cells(select) -> list[str]:
        return [str(select(r)) for r in runs]

    L = [f"axis consistency -- {len(runs)} runs",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {r['label']}: {r['dir']}, deutung.json sha256 "
          f"{r['digests']['deutung.json'][:16]}..." for r in runs]
    L += ["", _row("", [r["label"] for r in runs]),
          _row("interpretations", cells(lambda r: r["extra"]["n_interpretations"])),
          _row("without a semantic_status",
               cells(lambda r: r["extra"]["n_missing_status"]))]

    L += ["", "1. move pairs (finding 1)",
          _row("moved_away records",
               cells(lambda r: r["report"]["n_pairs"] + r["report"]["n_unpaired"]
                     + r["report"]["n_dangling"]), indent="      "),
          _row("joined over the pointer",
               cells(lambda r: r["report"]["n_pairs"]), indent="      "),
          _row("both sides interpreted",
               cells(lambda r: r["report"]["n_pairs_interpreted"]), indent="      "),
          _row("no pointer, not checked",
               cells(lambda r: r["report"]["n_unpaired"]), indent="      "),
          _row("pointer into a non-move (1a)",
               cells(lambda r: r["report"]["n_dangling"]), indent="      "),
          _row("moved_in without its source (AP-30)",
               cells(lambda r: r["report"].get("n_orphan", 0)), indent="      ")]
    for axis in PARTNER_AXES:
        L += [_row(f"{axis} disagrees",
                   cells(lambda r, a=axis: f"{r['report']['by_axis'][a]} = "
                         f"{_pct(r['report']['by_axis'][a], r['report']['n_pairs_interpreted'])} %"),
                   indent="      ")]
    signatures = sorted({s for r in runs for a in PARTNER_AXES
                         for s in r["extra"]["signatures"][a]})
    L += ["", "  signatures (moved_away / moved_in), any axis:"]
    L += [_row(f"{a} / {b}",
               cells(lambda r, a=a, b=b: sum(r["extra"]["signatures"][x][(a, b)]
                                             for x in PARTNER_AXES)), indent="      ")
          for a, b in signatures]

    L += ["", "2. axis B against axis A (finding 2)",
          _row("equivalent without counterpart",
               cells(lambda r: f"{r['report']['n_axis_b_contradicts_a']} = "
                     f"{_pct(r['report']['n_axis_b_contradicts_a'], r['extra']['n_interpretations'])} %"),
               indent="      ")]
    L += [_row(f"on {op}", cells(lambda r, o=op: r["extra"]["equivalent_on"][o]),
               indent="        ") for op in ("added", "removed")]

    L += ["", "3. axis C against the modality (finding 3)",
          _row("modal, called not_applicable",
               cells(lambda r: f"{r['report']['n_axis_c_contradicts_modality']} = "
                     f"{_pct(r['report']['n_axis_c_contradicts_modality'], r['extra']['n_interpretations'])} %"),
               indent="      "),
          _row("informative, yet directed",
               cells(lambda r: f"{r['report']['n_informative_with_direction']} = "
                     f"{_pct(r['report']['n_informative_with_direction'], r['extra']['n_interpretations'])} %"),
               indent="      ")]
    L += [_row(str(m), cells(lambda r, m=m: r["extra"]["modality_of_flagged"][m]),
               indent="        ")
          for m in sorted(MODAL_LABELS)]

    L += ["", "4. the free text against axis B (finding 4)",
          _row("narrowed, successor named (narrow)",
               cells(lambda r: r["extra"]["n_narrow"]), indent="      "),
          _row("of those also relaxed",
               cells(lambda r: r["extra"]["narrow_directions"]["relaxed"]),
               indent="      "),
          _row("wide form (measured only)",
               cells(lambda r: r["extra"]["n_wide"]), indent="      ")]

    L += ["", "5. the counter-calculation of the rule (finding 1)",
          _row("moved_away: replaced (turned around)",
               cells(lambda r: r["extra"]["moved_away_status"]["replaced"]),
               indent="      "),
          _row("moved_in: equivalent (the opposite)",
               cells(lambda r: r["extra"]["moved_in_status"]["equivalent"]),
               indent="      ")]

    L += ["", "6. the four flags together"]
    L += [_row(f, cells(lambda r, f=f: r["extra"]["flag_counts"][f]), indent="      ")
          for f in CONSISTENCY_FLAGS]
    L += [_row("more than one flag",
               cells(lambda r: r["report"]["n_multiple_flags"]), indent="      ")]
    return "\n".join(_format(L, width)) + "\n"


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
    reports = [(f"axis_consistency_{args.name or r['dir'].name}.txt", render(r))
               for r in runs]
    if len(runs) > 1:
        reports.append(("axis_consistency_comparison.txt", render_comparison(runs)))
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
