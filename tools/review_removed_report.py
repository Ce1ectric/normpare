"""review_removed_report.py -- how long the review list of AP-11 gets on a finished run.

The list itself is production code (``normpare.stages.review_removed``); this tool only
applies it to a run directory that was produced before AP-11 existed and counts what comes
out. Nothing is judged here that the pipeline would not judge -- the same functions, the
same thresholds.

Two numbers decide whether the list is usable at all:

* how many entries per corpus, split by reason and by *how many* reasons an entry carries.
  ``unbalanced_mapping`` is the wide criterion (AP-09 measured 39-56 % of all removals in
  surplus mappings), the two relocation reasons are the narrow ones (AP-10: 32 cases over
  three corpora).
* how many entries reach a coverage of exactly 1.00 outside their mapping. That is the
  ``11.3<11.3+11.3.2`` shape from AP-10: literally identical text behind a record
  boundary, unpairable no matter how good the aligner is.

``paragraph_balance`` is recomputed from the frozen ``norm_doc.json`` (as
``tools/mapping_balance.py`` does), so runs from before AP-09 are readable too. Reading
only, no LLM, no network.

Usage::

    poetry run python tools/review_removed_report.py --dir out/4110_2026-08b \\
        --out runs/AP-11_2026-08-12 --name 4110
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.report.review_list import render_review_list
from normpare.stages.diff import paragraph_balance
from normpare.stages.review_removed import (MIN_CHARS, OLD_SURPLUS_MIN, RELOCATION_TAU,
                                            REVIEW_REASONS, annotate_relocation,
                                            review_entries)

#: How many of the loudest mappings the report names.
TOP = 10


def rebalance(synopse: dict, old_doc: dict | None, new_doc: dict) -> None:
    """Recompute ``paragraph_balance`` on every chapter from the frozen documents."""
    if not old_doc:
        return
    o_secs = {s["id"]: s for s in old_doc.get("sections") or []}
    n_secs = {s["id"]: s for s in new_doc.get("sections") or []}
    for ch in synopse.get("chapters") or []:
        old_ids = ch.get("old_ids") or ([ch["old_id"]] if ch.get("old_id") else [])
        new_ids = ch.get("new_ids") or ([ch["new_id"]] if ch.get("new_id") else [])
        ch["paragraph_balance"] = paragraph_balance(
            [o_secs[i] for i in old_ids if i in o_secs],
            [n_secs[i] for i in new_ids if i in n_secs])


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f} %" if whole else "    -- "


def render(synopse: dict, entries: list[dict], seconds: float, src, digests: dict) -> str:
    removed = [c for ch in synopse.get("chapters") or []
               for c in ch.get("changes") or [] if c["kind"] == "removed"]
    judged = [c for c in removed if "relocation" in c]
    by_reason = Counter(r for e in entries for r in e["review_reasons"])
    by_count = Counter(len(e["review_reasons"]) for e in entries)
    only = Counter(tuple(e["review_reasons"]) for e in entries)
    verbatim = [c for c in judged
                if c["relocation"]["best_score"] == 1.0
                and not c["relocation"]["in_mapping"]]

    lines = [
        f"review list -- {src}",
        f"normpare {regression._version()}",
        *(f"  {name:<22} {sha}" for name, sha in digests.items()),
        "",
        f"removed changes:                      {len(removed)}",
        f"  judged (old_text >= {MIN_CHARS} characters): {len(judged)}",
        f"entries in the review list:           {len(entries)}   "
        f"{_pct(len(entries), len(judged))} of the judged",
        f"relocation measured in:               {seconds:.2f} s",
        "",
        f"1) by reason (tau = {RELOCATION_TAU}, old surplus >= {OLD_SURPLUS_MIN}; "
        "an entry may carry several)",
    ]
    for reason in REVIEW_REASONS:
        lines.append(f"     {reason:<28} {by_reason[reason]:5d}  "
                     f"{_pct(by_reason[reason], len(entries))}")
    lines += ["", "2) by number of reasons per entry"]
    for n in sorted(by_count):
        lines.append(f"     {n} reason(s)                    {by_count[n]:5d}  "
                     f"{_pct(by_count[n], len(entries))}")
    lines += ["", "3) the exact combinations"]
    for combo, n in sorted(only.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"     {n:5d}  {' + '.join(combo)}")
    lines += [
        "",
        "4) literally identical text outside the mapping (coverage 1.00)",
        f"     {len(verbatim)} case(s) -- unpairable behind a record boundary, "
        "not an aligner error",
    ]
    for c in verbatim[:TOP]:
        text = " ".join((c.get("old_text") or "").split())[:90]
        lines.append(f"     {c['relocation']['best_section']:<14} {text}")
    lines += ["", f"5) the {TOP} loudest mappings of the list"]
    loud = Counter(str(e["mapping_id"]) for e in entries)
    for mapping, n in loud.most_common(TOP):
        surplus = next(e["old_surplus"] for e in entries if str(e["mapping_id"]) == mapping)
        lines.append(f"     {n:5d}  {mapping:<40} old_surplus {surplus}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="finished run directory to read")
    ap.add_argument("--out", default=None, help="destination directory (default: print only)")
    ap.add_argument("--name", default=None, help="report name (default: directory name)")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    name = args.name or src.name
    synopse = json.loads((src / "synopse.json").read_text(encoding="utf-8"))
    new_doc = json.loads((src / "neu" / "norm_doc.json").read_text(encoding="utf-8"))
    old_path = src / "alt" / "norm_doc.json"
    old_doc = (json.loads(old_path.read_text(encoding="utf-8"))
               if old_path.exists() else None)

    rebalance(synopse, old_doc, new_doc)
    start = time.perf_counter()
    annotate_relocation(synopse.get("chapters") or [], new_doc)
    seconds = time.perf_counter() - start
    entries = review_entries(synopse)

    digests = {n: regression.sha256_file(src / n)
               for n in ("synopse.json", "neu/norm_doc.json") if (src / n).exists()}
    text = render(synopse, entries, seconds, src, digests)
    print(text)
    if args.out is None:
        return 0

    dest = regression.guard_write(Path(args.out))
    dest.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
            (f"pruefliste_{name}.txt", text),
            (f"pruefliste_{name}.md",
             render_review_list(entries, synopse.get("pair") or name, new_doc))):
        regression.guard_write(dest / filename).write_text(payload, encoding="utf-8")
        print(f"written: {dest / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
