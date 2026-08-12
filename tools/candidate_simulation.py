"""candidate_simulation.py -- what a wider candidate circle would buy, and what it costs.

Today the paragraph aligner pairs an old paragraph only with new paragraphs from the
``new_ids`` of **its own** record. AP-10 measured 32 removals across three corpora whose
text still stands in the new edition, outside that circle; AP-11 found 14 of them
literally identical (coverage 1.00). Those 14 are unpairable no matter how good the
aligner gets -- not a threshold question, a reach question.

Changing the mapper is expensive: every baseline breaks and every deviation has to be
judged one by one. The cost was known, the benefit never was. This tool measures the
benefit before it is paid for. Four candidate rules:

=======  =====================================================================
rule     candidate sections of a record
=======  =====================================================================
``A``    ``new_ids`` -- today's behaviour, the reference
``B``    A + every section the ``mapping_id`` names (either side) that ``new_ids``
         does not hold -- the merge group instead of the single record
``C``    A + every descendant of a section in A (``11.2.7`` -> ``11.2.7.6``)
``D``    B union C
=======  =====================================================================

Three numbers per rule and corpus:

* **benefit** -- how many relocated cases would come into reach, and how many of the
  literal ones (coverage 1.00), which are the undisputed defects;
* **cost** -- by how many paragraphs the candidate circle grows, on average and at worst.
  A wider circle means more pairing candidates and therefore more opportunity for *wrong*
  pairings: a genuine deletion would then be reported as ``similar``, which is worse than
  today's error;
* **dissolution** -- in how many records the widened circle holds sections that already
  belong to another record. That is the measure of how far a rule gives up the record
  boundary. For rule B it is high by construction, which is precisely why it has to be
  counted instead of guessed.

Nothing is implemented and no rule is recommended: the decision is made from the numbers,
elsewhere. Pure analysis -- the run directory is only ever read, ``src/normpare`` is not
touched, no LLM, no network.

Usage::

    poetry run python tools/candidate_simulation.py --dir out/4110_2026-08b \\
        --out runs/AP-12_2026-08-12 --name 4110
    poetry run python tools/candidate_simulation.py \\
        --dir out/4110_2026-08b --name 4110 --dir out/4120_2026-08 --name 4120 \\
        --out runs/AP-12_2026-08-12
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.stages.review_removed import (MIN_CHARS, RELOCATION_TAU,
                                            annotate_relocation)

#: The rules, in report order. ``A`` is the reference, not a proposal.
RULES = ("A", "B", "C", "D")

#: What each rule adds to ``new_ids``, in one line.
RULE_TEXT = {
    "A": "new_ids -- today's behaviour",
    "B": "+ sections named in the mapping_id (both sides)",
    "C": "+ descendants of the mapped sections",
    "D": "B union C",
}

#: Coverage from which a relocated case counts as found again. Same value the production
#: list uses; AP-10 swept 0.5 to 0.9 and found no knee.
TAU = RELOCATION_TAU

#: Coverage at which a case is literal -- the same text, character for character.
VERBATIM = 1.0


# -- the candidate circles -------------------------------------------------------------

def mapping_id_sections(mapping_id: str | None) -> list[str]:
    """Every section id a ``mapping_id`` names, both sides (``6<6.1+6.2`` -> three ids).

    The id is ``<new ids>`` ``<`` ``<old ids>``, each side sorted and joined by ``+``
    (:func:`normpare.stages.align.sections.mapping_id`). Beyond three ids a side is cut
    and a digest is appended (``+~1a2b3c4d``); the digest is not a section and is dropped,
    which makes this a lower bound for rule B on very wide merges.

    Both sides, because the old side is where the extra ids sit: the AP-10 proof case
    ``11.3<11.3+11.3.2`` maps the old sections 11.3 and 11.3.2 onto the single new 11.3,
    while the new edition *also* carries an 11.3.2 -- and that is where the text stands.
    The new side alone would make rule B a no-op: across all three reference runs it
    names a section beyond ``new_ids`` in 0 of 537 records, the old side in 100.
    """
    new_side, _, old_side = str(mapping_id or "").partition("<")
    return [part for part in new_side.split("+") + old_side.split("+")
            if part and not part.startswith("~")]


def descendants(section_ids, all_ids) -> list[str]:
    """Every id in ``all_ids`` below one of ``section_ids`` (``11.2.7`` -> ``11.2.7.6``).

    Dotted prefix, so ``11.2`` is not a parent of ``11.20``.
    """
    parents = tuple(f"{i}." for i in section_ids)
    return [i for i in all_ids if i.startswith(parents)]


def circle(rule: str, new_ids, mapping_id: str | None, all_ids) -> list[str]:
    """The candidate sections of one record under ``rule``, in document order.

    Only sections that exist in the new edition are returned: a ``mapping_id`` can name
    an id the document does not carry, and a candidate circle of phantoms would inflate
    the benefit of rule B.
    """
    known = [i for i in all_ids if i in set(new_ids)]
    out = set(known)
    if rule in ("B", "D"):
        out |= {i for i in mapping_id_sections(mapping_id) if i in set(all_ids)}
    if rule in ("C", "D"):
        out |= set(descendants(known, all_ids))
    return [i for i in all_ids if i in out]


# -- one row per relocated case ----------------------------------------------------------

def relocated_cases(synopse: dict, new_doc: dict) -> list[dict]:
    """Every ``removed`` report whose text stands outside the ``new_ids`` of its record.

    The measurement is the production one (:func:`annotate_relocation`), applied here to
    runs that were produced before it existed. It is idempotent, so a run that already
    carries ``relocation`` comes out unchanged.
    """
    chapters = synopse.get("chapters") or []
    annotate_relocation(chapters, new_doc)
    cases = []
    for ch in chapters:
        for change in ch.get("changes") or []:
            reloc = change.get("relocation") or {}
            if change.get("kind") != "removed" or not reloc:
                continue
            if reloc.get("best_score", 0.0) < TAU or reloc.get("in_mapping"):
                continue
            cases.append({
                "mapping_id": ch.get("mapping_id") or ch.get("new_id") or ch.get("old_id"),
                "chapter": ch.get("new_id") or ch.get("old_id"),
                "title": ch.get("title"),
                "old_ids": change.get("old_ids") or [],
                "new_ids": list(ch.get("new_ids") or
                                ([ch["new_id"]] if ch.get("new_id") else [])),
                "best_section": reloc.get("best_section"),
                "best_score": reloc.get("best_score", 0.0),
                "chars": len(change.get("old_text") or ""),
            })
    return cases


def _paragraph_counts(new_doc: dict) -> dict[str, int]:
    """``section id -> number of paragraphs the aligner would consider``."""
    # local import: keeps the module free of the numpy import align.paras carries until
    # it is actually needed
    from normpare.stages.align.paras import _paras

    return {s.get("id"): len(_paras(s)) for s in new_doc.get("sections") or []}


def _mean(values) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _covered(cases: list[dict], by_mapping: dict) -> int:
    """How many cases the circle of their own record reaches."""
    return sum(1 for case in cases
               if case["best_section"] in set(
                   by_mapping.get((case["mapping_id"], tuple(case["new_ids"])), ())))


def simulate(synopse: dict, new_doc: dict, old_doc: dict | None = None) -> dict:
    """Benefit, cost and dissolution of all four rules over one run."""
    all_ids = [s.get("id") for s in new_doc.get("sections") or []]
    counts = _paragraph_counts(new_doc)
    records = [ch for ch in synopse.get("chapters") or []]
    cases = relocated_cases(synopse, new_doc)
    verbatim = [c for c in cases if c["best_score"] >= VERBATIM]

    #: which sections belong to some record today -- the boundary rule B gives up
    assigned = {}
    for k, ch in enumerate(records):
        for i in (ch.get("new_ids") or ([ch["new_id"]] if ch.get("new_id") else [])):
            assigned.setdefault(i, set()).add(k)

    rules = {}
    for rule in RULES:
        circles = [circle(rule, ch.get("new_ids") or
                          ([ch["new_id"]] if ch.get("new_id") else []),
                          ch.get("mapping_id"), all_ids) for ch in records]
        by_mapping = {}
        for ch, sections in zip(records, circles):
            by_mapping[(ch.get("mapping_id"), tuple(ch.get("new_ids") or []))] = sections
        sizes, growth, foreign = [], [], 0
        in_circle: dict[str, int] = {}
        for k, (ch, sections) in enumerate(zip(records, circles)):
            base = ch.get("new_ids") or ([ch["new_id"]] if ch.get("new_id") else [])
            size = sum(counts.get(i, 0) for i in sections)
            sizes.append(size)
            growth.append(size - sum(counts.get(i, 0) for i in base if i in counts))
            added = set(sections) - set(base)
            if any(assigned.get(i, set()) - {k} for i in added):
                foreign += 1
            for i in sections:
                in_circle[i] = in_circle.get(i, 0) + 1

        rules[rule] = {
            "resolved": _covered(cases, by_mapping),
            "resolved_verbatim": _covered(verbatim, by_mapping),
            "growth_mean": _mean(growth),
            "growth_max": max(growth) if growth else 0,
            "growth_total": sum(growth),
            "circle_paragraphs_mean": _mean(sizes),
            "circle_paragraphs_max": max(sizes) if sizes else 0,
            "datasets_with_foreign_sections": foreign,
            "sections_in_several_circles": sum(1 for n in in_circle.values() if n > 1),
        }
    return {"rules": rules, "cases": cases,
            "relocated": len(cases), "relocated_verbatim": len(verbatim),
            "datasets": len(records), "sections": len(all_ids),
            "paragraphs": sum(counts.values())}


# -- report -------------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f} %" if whole else "    -- "


def render(result: dict, src, name: str, digests: dict) -> str:
    lines = [
        f"candidate simulation -- {name} ({src})",
        f"normpare {regression._version()}",
        *(f"  {n:<22} {sha}" for n, sha in digests.items()),
        "",
        f"records (datasets): {result['datasets']}",
        f"sections in the new edition: {result['sections']}, "
        f"paragraphs: {result['paragraphs']}",
        f"relocated removals at tau = {TAU} (>= {MIN_CHARS} characters): "
        f"{result['relocated']}",
        f"  thereof literal (coverage {VERBATIM:.2f}): {result['relocated_verbatim']}",
        "",
        "1) benefit -- relocated cases whose text lies inside the candidate circle",
        "     rule  resolved            thereof literal      what the rule adds",
    ]
    for rule in RULES:
        r = result["rules"][rule]
        lines.append(
            f"     {rule}     {r['resolved']:4d} / {result['relocated']:<4d} "
            f"{_pct(r['resolved'], result['relocated'])}   "
            f"{r['resolved_verbatim']:4d} / {result['relocated_verbatim']:<4d} "
            f"{_pct(r['resolved_verbatim'], result['relocated_verbatim'])}   "
            f"{RULE_TEXT[rule]}")
    lines += [
        "",
        "2) cost -- paragraphs in the candidate circle of a record",
        "     rule   mean     max    growth mean   growth max   growth total",
    ]
    for rule in RULES:
        r = result["rules"][rule]
        lines.append(f"     {rule}    {r['circle_paragraphs_mean']:7.2f} {r['circle_paragraphs_max']:6d}   "
                     f"{r['growth_mean']:11.2f} {r['growth_max']:12d} {r['growth_total']:14d}")
    lines += [
        "",
        "3) dissolution -- how far the rule gives up the record boundary",
        "     rule   records whose circle holds a foreign section   sections in >1 circle",
    ]
    for rule in RULES:
        r = result["rules"][rule]
        lines.append(f"     {rule}    {r['datasets_with_foreign_sections']:8d} "
                     f"{_pct(r['datasets_with_foreign_sections'], result['datasets'])}"
                     f"{'':26s}{r['sections_in_several_circles']:6d}")

    lines += ["", "4) the relocated cases, and the first rule that reaches them"]
    for case in sorted(result["cases"],
                       key=lambda c: (-c["best_score"], str(c["mapping_id"]),
                                      [str(i) for i in c["old_ids"]])):
        reached = [rule for rule in RULES
                   if case["best_section"] in set(
                       circle(rule, case["new_ids"], case["mapping_id"],
                              [case["best_section"], *case["new_ids"]]))]
        lines.append(f"     {case['best_score']:.2f}  {str(case['mapping_id']):<28s} "
                     f"-> {str(case['best_section']):<22s} "
                     f"{('+'.join(reached) or 'none'):<12s} {case['chars']:5d} chars")
    return "\n".join(lines) + "\n"


def render_comparison(results: list[tuple[str, dict]]) -> str:
    """The four rules across all corpora -- the table the decision is made from."""
    lines = ["candidate simulation -- comparison of the four rules",
             f"normpare {regression._version()}",
             "",
             *(f"  {rule}: {RULE_TEXT[rule]}" for rule in RULES),
             "",
             "benefit (relocated cases reached / all)  and  literal cases (coverage 1.00)",
             "     corpus       cases  literal " +
             "".join(f"    {rule:>5s}          " for rule in RULES)]
    for name, result in results:
        cells = "".join(
            f"  {result['rules'][rule]['resolved']:4d} / "
            f"{result['rules'][rule]['resolved_verbatim']:<4d}   "
            for rule in RULES)
        lines.append(f"     {name:<12s} {result['relocated']:5d} "
                     f"{result['relocated_verbatim']:7d}  {cells}")
    total = {rule: (sum(r["rules"][rule]["resolved"] for _, r in results),
                    sum(r["rules"][rule]["resolved_verbatim"] for _, r in results))
             for rule in RULES}
    lines.append(f"     {'all three':<12s} {sum(r['relocated'] for _, r in results):5d} "
                 f"{sum(r['relocated_verbatim'] for _, r in results):7d}  "
                 + "".join(f"  {total[rule][0]:4d} / {total[rule][1]:<4d}   "
                           for rule in RULES))

    lines += ["", "cost (paragraphs added to a candidate circle: mean / max)",
              "     corpus      " + "".join(f"    {rule:>5s}          " for rule in RULES)]
    for name, result in results:
        lines.append(f"     {name:<12s}  " + "".join(
            f"  {result['rules'][rule]['growth_mean']:6.2f} / "
            f"{result['rules'][rule]['growth_max']:<4d}   " for rule in RULES))

    lines += ["", "dissolution (records whose circle holds a section of another record)",
              "     corpus      records" +
              "".join(f"    {rule:>5s}          " for rule in RULES)]
    for name, result in results:
        lines.append(f"     {name:<12s} {result['datasets']:6d}  " + "".join(
            f"  {result['rules'][rule]['datasets_with_foreign_sections']:4d} "
            f"{_pct(result['rules'][rule]['datasets_with_foreign_sections'], result['datasets'])}  "
            for rule in RULES))
    lines += ["",
              "No rule is implemented and none is recommended here -- this is a measurement.",
              "A wider circle also creates the opportunity for a wrong pairing, and a",
              "genuine deletion reported as 'similar' is worse than today's error."]
    return "\n".join(lines) + "\n"


def load(out_dir) -> tuple[dict, dict, dict | None]:
    """``synopse.json``, ``neu/norm_doc.json`` and -- if present -- ``alt``."""
    run = Path(out_dir)
    for name in ("synopse.json", "neu/norm_doc.json"):
        if not (run / name).exists():
            raise SystemExit(f"{run / name} is missing -- the simulation needs the "
                             "synopsis and the new edition of a finished run.")
    old = run / "alt" / "norm_doc.json"
    return (json.loads((run / "synopse.json").read_text(encoding="utf-8")),
            json.loads((run / "neu" / "norm_doc.json").read_text(encoding="utf-8")),
            json.loads(old.read_text(encoding="utf-8")) if old.exists() else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, action="append",
                    help="finished run directory to read (repeatable)")
    ap.add_argument("--name", action="append", default=None,
                    help="report name per --dir (default: directory name)")
    ap.add_argument("--out", default=None,
                    help="destination directory for the reports (default: print only)")
    args = ap.parse_args(argv)

    names = list(args.name or [])
    names += [Path(d).name for d in args.dir[len(names):]]
    dest = None
    if args.out is not None:
        dest = regression.guard_write(Path(args.out))
        dest.mkdir(parents=True, exist_ok=True)

    written, results = [], []
    for run_dir, name in zip(args.dir, names):
        src = Path(run_dir)
        synopse, new_doc, old_doc = load(src)
        result = simulate(synopse, new_doc, old_doc)
        digests = {n: regression.sha256_file(src / n)
                   for n in ("synopse.json", "neu/norm_doc.json") if (src / n).exists()}
        text = render(result, src, name, digests)
        print(text)
        results.append((name, result))
        if dest is not None:
            path = regression.guard_write(dest / f"simulation_{name}.txt")
            path.write_text(text, encoding="utf-8")
            written.append(path)

    if dest is not None:
        path = regression.guard_write(dest / "simulation_vergleich.txt")
        path.write_text(render_comparison(results), encoding="utf-8")
        written.append(path)
        print("written: " + "\n         ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
