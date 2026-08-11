"""
evidence_report.py -- recompute the evidence metrics over an existing run.

Reads ``deutung.json`` and ``chapters.json`` of a finished output directory and
recomputes, for every interpretation, the four quantities of
:func:`normpare.stages.deutung.check_evidence`: ``evidence_ok`` (the historical
15-character rule), ``evidence_strict``, ``evidence_match_chars`` and
``evidence_fragments``. No LLM, no network -- the quotes and the change records are
both already on disk.

The recomputed ``evidence_ok`` is compared against the value stored in
``deutung.json``; a mismatch would mean the guard's historical branch has changed
and is reported as such.

Interpretations whose chapter has no counterpart in ``chapters.json`` cannot be
checked (there is no change record to check against). They are listed in the JSON
with ``chapter_found: false`` and excluded from every quota.

The join runs over ``mapping_id`` (ENT-24), which the interpretation stage sets
itself, and falls back to ``section_id`` for runs recorded before AP-06 -- there the
key was whatever the model echoed, and in the 4110 run it echoed a slug of the
heading or the value of the ``Teil`` field, which left 165 interpretations without a
chapter.

The source directory is only ever read; both output files go to ``--out``, and
``regression.guard_write`` refuses any target below ``out/``.

Usage::

    python tools/evidence_report.py --dir out/4110_hot --out runs/AP-02_2026-08-10
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import regression

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from normpare.stages.deutung import change_haystack, check_evidence
from normpare.text.textnorm import n2

#: Keys of the five recomputed quantities, in report order.
METRICS = ("evidence_ok", "evidence_strict", "evidence_match_chars", "evidence_fragments",
           "evidence_unique")


def _has_ellipsis(ev: str) -> bool:
    return "…" in ev or "..." in ev


def collect(out_dir) -> list[dict]:
    """One row per interpretation, in the order both files list them."""
    out = Path(out_dir)
    for name in ("deutung.json", "chapters.json"):
        if not (out / name).exists():
            raise SystemExit(f"{out / name} is missing -- the report needs both "
                             "deutung.json and chapters.json of a finished run.")
    deut = json.loads((out / "deutung.json").read_text(encoding="utf-8"))
    chap = json.loads((out / "chapters.json").read_text(encoding="utf-8"))
    chapters = chap.get("chapters") or []
    by_mapping = {c["mapping_id"]: c for c in chapters if c.get("mapping_id")}
    by_id = {c.get("id"): c for c in chapters}

    rows = []
    for c in deut.get("chapters") or []:
        sid = c.get("section_id")
        ch = by_mapping.get(c.get("mapping_id")) or by_id.get(sid)
        for d in (c.get("interpretations") or c.get("deutungen") or []):
            ev = n2(d.get("evidence") or "").lower()
            row = {"section_id": sid, "mapping_id": c.get("mapping_id"),
                   "change_index": d.get("change_index"),
                   "chapter_found": ch is not None}
            row.update(check_evidence(d, ch) if ch is not None
                       else dict.fromkeys(METRICS, None))
            # the ellipsis-blind reading of "strict": the whole quote, ellipsis marks
            # included, occurs verbatim. This is what AP-00 measured (branch "full").
            row["evidence_contiguous"] = (ch is not None and bool(ev)
                                          and ev in change_haystack(d, ch))
            row["evidence_chars"] = len(ev)
            row["has_ellipsis"] = _has_ellipsis(ev)
            row["stored_evidence_ok"] = bool(d.get("evidence_ok"))
            rows.append(row)
    return rows


def _dist(values: list[int]) -> dict:
    """min/q1/median/q3/max of a list of numbers (empty list -> all ``None``)."""
    if not values:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}
    q1, med, q3 = (statistics.quantiles(values, n=4) if len(values) > 1
                   else (values[0], values[0], values[0]))
    return {"n": len(values), "min": min(values), "q1": round(q1, 1),
            "median": round(med, 1), "q3": round(q3, 1), "max": max(values)}


def summarize(rows: list[dict]) -> dict:
    """Quotas and distributions over the checkable interpretations."""
    checked = [r for r in rows if r["chapter_found"]]
    ok = [r for r in checked if r["evidence_ok"]]
    strict = [r for r in checked if r["evidence_strict"]]
    only_ok = [r for r in checked if r["evidence_ok"] and not r["evidence_strict"]]
    only_strict = [r for r in checked if r["evidence_strict"] and not r["evidence_ok"]]
    ellipsis = [r for r in checked if r["has_ellipsis"]]
    n = len(checked) or 1
    return {
        "interpretations": len(rows),
        "checked": len(checked),
        "without_chapter": len(rows) - len(checked),
        "chapters_without_counterpart": len({r["section_id"] for r in rows
                                             if not r["chapter_found"]}),
        "parity_mismatches": sum(1 for r in checked
                                 if r["evidence_ok"] != r["stored_evidence_ok"]),
        "evidence_ok": len(ok), "evidence_ok_pct": round(100 * len(ok) / n, 2),
        "evidence_strict": len(strict),
        "evidence_strict_pct": round(100 * len(strict) / n, 2),
        # ENT-30: of the strict hits, how many are not localizable -- the upper bound
        # for undetectable change_index errors
        "evidence_unique": sum(1 for r in strict if r["evidence_unique"]),
        "strict_not_unique": sum(1 for r in strict if not r["evidence_unique"]),
        "strict_not_unique_pct": round(
            100 * sum(1 for r in strict if not r["evidence_unique"]) / (len(strict) or 1), 2),
        "evidence_contiguous": sum(1 for r in checked if r["evidence_contiguous"]),
        "evidence_contiguous_pct": round(
            100 * sum(1 for r in checked if r["evidence_contiguous"]) / n, 2),
        "match_chars_all": _dist([r["evidence_match_chars"] for r in checked]),
        "fragments_gt_1": sum(1 for r in checked if r["evidence_fragments"] > 1),
        "ok_but_not_strict": len(only_ok),
        "ok_but_not_strict_match_chars": _dist([r["evidence_match_chars"] for r in only_ok]),
        "ok_but_not_strict_evidence_chars": _dist([r["evidence_chars"] for r in only_ok]),
        "ok_but_not_strict_covered_pct": _dist(
            [round(100 * r["evidence_match_chars"] / max(r["evidence_chars"], 1))
             for r in only_ok]),
        "strict_but_not_ok": len(only_strict),
        "strict_but_not_ok_evidence_chars": _dist([r["evidence_chars"] for r in only_strict]),
        "with_ellipsis": len(ellipsis),
        "with_ellipsis_strict": sum(1 for r in ellipsis if r["evidence_strict"]),
        "with_ellipsis_ok": sum(1 for r in ellipsis if r["evidence_ok"]),
    }


def _dist_line(label: str, d: dict) -> str:
    if not d["n"]:
        return f"  {label:<34} (none)"
    return (f"  {label:<34} min {d['min']} | q1 {d['q1']} | median {d['median']} "
            f"| q3 {d['q3']} | max {d['max']}  (n={d['n']})")


def render(rows: list[dict], summary: dict, out_dir: Path, digests: dict) -> str:
    """The human-readable summary (``evidence_<run>.txt``)."""
    s = summary
    checked = [r for r in rows if r["chapter_found"]]
    only_ok = sorted((r for r in checked if r["evidence_ok"] and not r["evidence_strict"]),
                     key=lambda r: (r["evidence_match_chars"] / max(r["evidence_chars"], 1),
                                    r["section_id"], r["change_index"] or 0))
    only_strict = [r for r in checked if r["evidence_strict"] and not r["evidence_ok"]]

    def case(r: dict) -> str:
        return (f"    {r['section_id']!s:<22}{r['change_index']!s:>6}"
                f"{r['evidence_match_chars']:>14} / {r['evidence_chars']}")

    L = [f"evidence report -- {out_dir}",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {name}: sha256 {sha}" for name, sha in sorted(digests.items())]
    L += [
        "",
        f"interpretations in deutung.json      {s['interpretations']}",
        f"  with a chapter in chapters.json    {s['checked']}   <- basis of all quotas",
        (f"  without a chapter                  {s['without_chapter']} "
         f"(in {s['chapters_without_counterpart']} chapters, not checkable)"),
        ("parity: recomputed evidence_ok == stored evidence_ok in "
         f"{s['checked'] - s['parity_mismatches']} of {s['checked']} "
         f"({s['parity_mismatches']} mismatch(es))"),
        "",
        f"evidence_ok      {s['evidence_ok']:>6} / {s['checked']} = {s['evidence_ok_pct']} %",
        (f"evidence_strict  {s['evidence_strict']:>6} / {s['checked']} = "
         f"{s['evidence_strict_pct']} %   (fragments checked separately)"),
        (f"  without splitting at ellipsis marks: {s['evidence_contiguous']} / "
         f"{s['checked']} = {s['evidence_contiguous_pct']} %  "
         "(the AP-00 reading, branch 'full')"),
        (f"evidence_unique  {s['evidence_unique']:>6} / {s['evidence_strict']} strict hits"
         f"   ({s['strict_not_unique']} = {s['strict_not_unique_pct']} % also fit a "
         "foreign record of the same chapter -- ENT-30, a marker, not a gate)"),
        "",
        "evidence_match_chars over all checked interpretations:",
        _dist_line("characters covered", s["match_chars_all"]),
        f"  evidence_fragments > 1             {s['fragments_gt_1']}",
        (f"  quotes containing an ellipsis      {s['with_ellipsis']}, of which strict "
         f"{s['with_ellipsis_strict']}, ok {s['with_ellipsis_ok']}"),
        "",
        ("difference set (evidence_ok = true, evidence_strict = false): "
         f"{s['ok_but_not_strict']}"),
        _dist_line("characters covered", s["ok_but_not_strict_match_chars"]),
        _dist_line("quote length", s["ok_but_not_strict_evidence_chars"]),
        _dist_line("share of the quote covered (%)", s["ok_but_not_strict_covered_pct"]),
        "",
        "  the twenty least covered cases:",
        "    section_id            change_index   covered / quote chars",
    ]
    L += [case(r) for r in only_ok[:20]]
    L += [
        "",
        ("reverse set (evidence_strict = true, evidence_ok = false): "
         f"{s['strict_but_not_ok']}"),
        _dist_line("quote length", s["strict_but_not_ok_evidence_chars"]),
    ]
    L += [case(r) for r in only_strict[:20]]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="run output directory to read")
    ap.add_argument("--out", required=True, help="destination directory for the report")
    ap.add_argument("--name", default=None, help="report name (default: directory name)")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    name = args.name or src.name
    dest = regression.guard_write(Path(args.out))
    dest.mkdir(parents=True, exist_ok=True)

    rows = collect(src)
    summary = summarize(rows)
    digests = {n: regression.sha256_file(src / n)
               for n in ("deutung.json", "chapters.json")}

    payload = {"run": str(src), "normpare_version": regression._version(),
               "sources": digests, "summary": summary, "interpretations": rows}
    json_path = regression.guard_write(dest / f"evidence_{name}.json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    text = render(rows, summary, src, digests)
    txt_path = regression.guard_write(dest / f"evidence_{name}.txt")
    txt_path.write_text(text, encoding="utf-8")

    print(text)
    print(f"written: {json_path}\n         {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
