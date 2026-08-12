"""removed_relocation.py -- which stage produced a removal: the mapping or the aligner.

AP-09 established that ``old_surplus`` -- the excess of old paragraphs in a chapter
mapping -- predicts ``removed`` reports, and does so without measuring itself. It left
the decisive question open: *which stage causes them?* There are exactly two candidates,
and they ask for opposite repairs.

===============================  ==========================================================
verdict                          reading
===============================  ==========================================================
``relocated_outside_mapping``    the text stands in the new edition, but in a section that
                                 is **not** among the ``new_ids`` of its mapping -- the
                                 paragraph aligner never got to see it. The chapter
                                 mapping is at fault.
``missed_inside_mapping``        the text stands in a section that **is** among the
                                 ``new_ids`` -- the mapping was right, the pairing failed.
``not_found``                    no textual evidence; presumably really removed.
===============================  ==========================================================

Coverage is measured over **word trigrams** of the N3 form,
``|T(old) & T(candidate)| / |T(old)|``. Word *sets* are deliberately not used: they
measure vocabulary instead of textual identity and produced a three-digit false finding
in the pre-study (``notizen/Dritter_Korpus_60909.md``). Candidates are the paragraphs of
the new edition that the aligner itself considers (``align.paras._paras``).

No threshold is written down: the report sweeps tau over
:data:`THRESHOLDS` and shows the class distribution for each, split by ``old_surplus`` as
well, because that is what steers the repair. The case list (``faelle_<name>.md``) is
built at :data:`CASE_TAU`.

Pure analysis: the run directory is only ever read, nothing in ``src/normpare`` is
touched, no LLM, no network.

Usage::

    poetry run python tools/removed_relocation.py --dir out/4110_2026-08b
    poetry run python tools/removed_relocation.py --dir out/4110_2026-08b \\
        --out runs/AP-10_2026-08-12 --name 4110
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression, audit
sys.path.insert(0, str(ROOT / "src"))

import regression
import removed_audit

from normpare.stages.align.paras import _paras
from normpare.stages.diff import paragraph_balance
from normpare.text.textnorm import n3

#: A removal shorter than this many characters of ``old_text`` is not judged: a handful
#: of words covers itself by chance somewhere in a document of a quarter million
#: characters, in both directions.
MIN_CHARS = 60

#: The sweep. No single value is written down -- AP-09 found no knee in the paragraph
#: arithmetic, and a threshold that is set rather than found does not belong in code.
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)

#: The threshold the readable case list is built at.
CASE_TAU = 0.7

#: Verdict classes, in report order.
VERDICTS = ("relocated_outside_mapping", "missed_inside_mapping", "not_found")

#: The old surplus that AP-09 measured as the sharpest separator.
SURPLUS_MIN = 5

#: How many characters of the removed text go into the case list.
CASE_CHARS = 200


# -- coverage over word trigrams ---------------------------------------------------------

def trigrams(text: str) -> set[tuple[str, ...]]:
    """Word trigrams of the N3 form of ``text``.

    N3 is the semantic normalisation level of the productive diff: lowercased, numbers
    canonicalised, punctuation dropped. Below three words the whole word sequence is the
    single "trigram", so a short span still matches itself instead of matching nothing.
    """
    words = n3(text).split()
    if len(words) < 3:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def candidates(new_doc: dict) -> list[dict]:
    """Every paragraph of the new edition the aligner would consider, in document order."""
    out = []
    for sec in new_doc.get("sections") or []:
        for p in _paras(sec):
            out.append({"id": p.get("id"), "section": sec.get("id"),
                        "section_title": sec.get("title"),
                        "trigrams": trigrams(p.get("n1") or p.get("n0") or "")})
    return out


def _postings(cands: list[dict]) -> dict[tuple[str, ...], list[int]]:
    """``trigram -> candidate positions``. Turns the sweep into one pass per removal."""
    postings: dict[tuple[str, ...], list[int]] = {}
    for k, c in enumerate(cands):
        for t in c["trigrams"]:
            postings.setdefault(t, []).append(k)
    return postings


def best_match(needle: set[tuple[str, ...]], cands: list[dict],
               postings: dict, exclude: set[str] | None = None) -> tuple[float, dict | None]:
    """Best covering candidate paragraph, optionally outside a set of sections.

    Ties are broken by document order, so the result does not depend on dict iteration.
    """
    if not needle:
        return 0.0, None
    hits: Counter[int] = Counter()
    for t in needle:
        for k in postings.get(t, ()):
            hits[k] += 1
    best, best_k = 0.0, None
    for k, n in sorted(hits.items()):
        if exclude and cands[k]["section"] in exclude:
            continue
        score = n / len(needle)
        if score > best:
            best, best_k = score, k
    return round(best, 4), (cands[best_k] if best_k is not None else None)


# -- one row per judged removal -------------------------------------------------------------

def _old_surplus(chapter: dict, o_secs: dict, n_secs: dict) -> int:
    """``old_surplus`` of the mapping, recomputed from the frozen documents.

    Recomputing (as ``tools/mapping_balance.py`` does) keeps runs readable that were
    produced before ``paragraph_balance`` existed; a stored value is used only when the
    documents are not at hand.
    """
    if o_secs or n_secs:
        return paragraph_balance(
            [o_secs[i] for i in (chapter.get("old_ids") or []) if i in o_secs],
            [n_secs[i] for i in (chapter.get("new_ids") or []) if i in n_secs])["old_surplus"]
    return (chapter.get("paragraph_balance") or {}).get("old_surplus") or 0


def evaluate(synopse: dict, new_doc: dict, old_doc: dict | None = None) -> dict:
    """One row per ``removed`` report of at least :data:`MIN_CHARS` characters."""
    cands = candidates(new_doc)
    postings = _postings(cands)
    o_secs = {s["id"]: s for s in (old_doc or {}).get("sections") or []}
    n_secs = {s["id"]: s for s in new_doc.get("sections") or []}

    rows: list[dict] = []
    total = skipped = 0
    for ch in synopse.get("chapters") or []:
        new_ids = list(ch.get("new_ids") or
                       ([ch["new_id"]] if ch.get("new_id") else []))
        surplus = _old_surplus(ch, o_secs, n_secs)
        for change in ch.get("changes") or []:
            if change["kind"] != "removed":
                continue
            total += 1
            text = change.get("old_text") or ""
            if len(text) < MIN_CHARS:
                skipped += 1
                continue
            needle = trigrams(text)
            score, hit = best_match(needle, cands, postings)
            out_score, out_hit = best_match(needle, cands, postings, exclude=set(new_ids))
            rows.append({
                "mapping_id": (ch.get("mapping_id") or ch.get("new_id")
                               or ch.get("old_id")),
                "chapter_id": ch.get("new_id") or ch.get("old_id"),
                "title": ch.get("title"),
                "mode": ch.get("mode"),
                "old_ids": change.get("old_ids") or [],
                "new_ids": new_ids,
                "old_text": text,
                "chars": len(text),
                "best_score": score,
                "best_section": hit["section"] if hit else None,
                "best_section_title": hit["section_title"] if hit else None,
                "best_paragraph": hit["id"] if hit else None,
                "in_mapping": bool(hit and hit["section"] in set(new_ids)),
                "best_outside_score": out_score,
                "best_outside_section": out_hit["section"] if out_hit else None,
                "old_surplus": surplus,
            })
    return {"rows": rows, "total_removed": total, "skipped_short": skipped,
            "candidates": len(cands)}


def verdict(row: dict, tau: float) -> str:
    """The mechanical classification, from ``best_score`` and ``in_mapping`` alone."""
    if row["best_score"] < tau:
        return "not_found"
    return "missed_inside_mapping" if row["in_mapping"] else "relocated_outside_mapping"


def sweep(rows: list[dict], thresholds=THRESHOLDS) -> dict[float, dict[str, int]]:
    """Class distribution per threshold, thresholds ascending."""
    return {tau: {v: sum(1 for r in rows if verdict(r, tau) == v) for v in VERDICTS}
            for tau in sorted(thresholds)}


def cases(rows: list[dict], tau: float = CASE_TAU) -> list[dict]:
    """The ``relocated_outside_mapping`` rows, best coverage first."""
    hits = [r for r in rows if verdict(r, tau) == "relocated_outside_mapping"]
    return sorted(hits, key=lambda r: (-r["best_score"], str(r["mapping_id"]),
                                       r["old_ids"]))


# -- cross check against the removal audit ---------------------------------------------------

#: Audit verdicts, in report order; ``kurzfragment`` covers the spans the audit refuses
#: to judge (ADR-0002 shape) but the relocation does judge.
AUDIT_CLASSES = ("echt_entfallen", "falsch_positiv", "verschiebung", "kurzfragment")


def _audit_key(chapter_id, old_ids, text) -> tuple:
    return (chapter_id, tuple(old_ids or []), text)


def cross_table(rows: list[dict], audit_result: dict, tau: float = CASE_TAU) -> dict:
    """``audit verdict -> relocation verdict -> count`` over the judged removals."""
    by_key = {_audit_key(f["kapitel"], f["absatz_ids"], f["text"]): f["klasse"]
              for f in audit_result.get("findings") or []}
    table = {a: {v: 0 for v in VERDICTS} for a in AUDIT_CLASSES}
    for r in rows:
        klasse = by_key.get(_audit_key(r["chapter_id"], r["old_ids"], r["old_text"]),
                            "kurzfragment")
        table[klasse][verdict(r, tau)] += 1
    return table


# -- report -------------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f} %" if whole else "    -- "


def _sweep_block(rows: list[dict], indent: str = "     ") -> list[str]:
    lines = [f"{indent}tau   relocated_outside   missed_inside      not_found",
             f"{indent}      (mapping is at fault) (aligner is at fault)"]
    for tau, counts in sweep(rows).items():
        cells = "".join(f"  {counts[v]:5d} {_pct(counts[v], len(rows))}" for v in VERDICTS)
        lines.append(f"{indent}{tau:.1f} {cells}")
    return lines


def render(result: dict, src, digests: dict, audit_result: dict | None) -> str:
    rows = result["rows"]
    lines = [
        f"removed relocation -- {src}",
        f"normpare {regression._version()}",
        *(f"  {name:<22} {sha}" for name, sha in digests.items()),
        "",
        f"removed changes in the synopsis: {result['total_removed']}",
        f"  judged (old_text >= {MIN_CHARS} characters): {len(rows)}",
        f"  skipped as too short:                {result['skipped_short']}",
        f"candidate paragraphs in the new edition: {result['candidates']}",
        "",
        "1) class distribution over tau (all judged removals)",
        *_sweep_block(rows),
        "",
        f"2) the same, split by the old surplus of the mapping (AP-09, >= {SURPLUS_MIN})",
    ]
    groups = (("old_surplus >= %d" % SURPLUS_MIN,
               [r for r in rows if r["old_surplus"] >= SURPLUS_MIN]),
              ("old_surplus == 0", [r for r in rows if r["old_surplus"] == 0]),
              ("in between", [r for r in rows if 0 < r["old_surplus"] < SURPLUS_MIN]))
    for name, group in groups:
        lines += [f"   {name}  (N = {len(group)})"]
        lines += _sweep_block(group) if group else ["     (none)"]
    lines += ["", "3) distribution of the best coverage found"]
    edges = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.3, 0.0)
    for lo, hi in zip(edges[1:], edges[:-1]):
        grp = [r for r in rows if lo <= r["best_score"] < hi or (hi == 1.0
                                                                 and r["best_score"] == 1.0)]
        lines.append(f"     {lo:.1f} .. {hi:.1f}   {len(grp):5d}  {_pct(len(grp), len(rows))}"
                     f"   thereof inside the mapping: "
                     f"{sum(1 for r in grp if r['in_mapping']):4d}")

    reloc = cases(rows)
    lines += [
        "",
        f"4) the case list at tau = {CASE_TAU}",
        f"     relocated_outside_mapping: {len(reloc)}",
        f"     thereof from a mapping with old_surplus >= {SURPLUS_MIN}: "
        f"{sum(1 for r in reloc if r['old_surplus'] >= SURPLUS_MIN)}",
        f"     thereof from a mapping without any counterpart chapter: "
        f"{sum(1 for r in reloc if not r['new_ids'])}",
    ]

    if audit_result is not None:
        table = cross_table(rows, audit_result)
        lines += [
            "",
            f"5) cross table against removed_audit, tau = {CASE_TAU} (absolute counts)",
            "     audit \\ relocation      relocated_outside  missed_inside  not_found",
        ]
        for name in AUDIT_CLASSES:
            row = table[name]
            lines.append(f"     {name:<22} {row['relocated_outside_mapping']:12d} "
                         f"{row['missed_inside_mapping']:14d} {row['not_found']:10d}")
        blind = table["echt_entfallen"]["relocated_outside_mapping"]
        lines += [
            "",
            f"     the blind spot of the audit: {blind} span(s) the audit calls really",
            "     removed while the relocation finds them again outside the mapping --",
            "     the audit only asks whether text is findable, not whether the chapter",
            "     pairing is right.",
        ]
    return "\n".join(lines) + "\n"


def render_cases(rows: list[dict], src, name: str, tau: float = CASE_TAU) -> str:
    """The readable case list -- German, it is a review list, not a published artifact."""
    reloc = cases(rows, tau)
    out = [f"# Fälle `relocated_outside_mapping` — {name}",
           "",
           f"Quelle: `{src}`, τ = {tau} · {len(reloc)} Fälle, absteigend nach "
           "Trigrammüberdeckung.",
           "",
           "Der Text steht im neuen Dokument, aber in einem Abschnitt, der **nicht** zu den",
           "`new_ids` der Kapitelzuordnung gehört — der Absatz-Aligner konnte ihn nie sehen.",
           ""]
    if not reloc:
        out += ["(keine)", ""]
    for i, r in enumerate(reloc, 1):
        text = " ".join((r["old_text"] or "").split())
        if len(text) > CASE_CHARS:
            text = text[:CASE_CHARS] + " …"
        out += [
            f"## {i}. `{r['mapping_id']}` — {r['title']}",
            "",
            f"- Alt-Überschuss der Zuordnung: **{r['old_surplus']}**",
            f"- gefunden in `{r['best_section']}` — {r['best_section_title']} "
            f"(Absatz `{r['best_paragraph']}`), Überdeckung **{r['best_score']:.2f}**",
            "- `new_ids` der Zuordnung: "
            + (", ".join(f"`{i}`" for i in r["new_ids"]) or "_(keine)_"),
            f"- entfallene Absätze: " + ", ".join(f"`{i}`" for i in r["old_ids"]),
            "",
            f"> {text}",
            "",
        ]
    return "\n".join(out)


def load(out_dir) -> tuple[dict, dict, dict | None, dict | None]:
    """``synopse.json``, ``neu/norm_doc.json`` and -- if present -- ``alt``/``mapping``."""
    run = Path(out_dir)
    for name in ("synopse.json", "neu/norm_doc.json"):
        if not (run / name).exists():
            raise SystemExit(f"{run / name} is missing -- the relocation analysis needs "
                             "the synopsis and the new edition of a finished run.")
    optional = {}
    for key, name in (("old", "alt/norm_doc.json"), ("mapping", "mapping.json")):
        path = run / name
        optional[key] = (json.loads(path.read_text(encoding="utf-8"))
                         if path.exists() else None)
    return (json.loads((run / "synopse.json").read_text(encoding="utf-8")),
            json.loads((run / "neu/norm_doc.json").read_text(encoding="utf-8")),
            optional["old"], optional["mapping"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="finished run directory to read")
    ap.add_argument("--out", default=None,
                    help="destination directory for the report (default: print only)")
    ap.add_argument("--name", default=None, help="report name (default: directory name)")
    ap.add_argument("--no-audit", action="store_true",
                    help="skip the cross check against tools/removed_audit.py")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    name = args.name or src.name
    synopse, new_doc, old_doc, mapping = load(src)
    result = evaluate(synopse, new_doc, old_doc)
    audit_result = (None if args.no_audit
                    else removed_audit.audit(synopse, new_doc, mapping))
    digests = {n: regression.sha256_file(src / n)
               for n in ("synopse.json", "neu/norm_doc.json") if (src / n).exists()}

    text = render(result, src, digests, audit_result)
    print(text)
    if args.out is None:
        return 0

    dest = regression.guard_write(Path(args.out))
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, payload in (
            (f"relocation_{name}.txt", text),
            (f"faelle_{name}.md", render_cases(result["rows"], src, name)),
            (f"relocation_{name}.json", json.dumps(
                {"run": str(src), "normpare_version": regression._version(),
                 "sources": digests,
                 "summary": {k: v for k, v in result.items() if k != "rows"},
                 "thresholds": {str(t): c for t, c in sweep(result["rows"]).items()},
                 "rows": result["rows"]},
                ensure_ascii=False, indent=1) + "\n")):
        path = regression.guard_write(dest / filename)
        path.write_text(payload, encoding="utf-8")
        written.append(path)
    print("written: " + "\n         ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
