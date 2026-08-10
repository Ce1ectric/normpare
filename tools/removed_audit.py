"""removed_audit.py -- check every reported removal against the new edition.

A ``removed`` change claims that a passage of the old edition is gone. If the passage
still stands in the new edition, that claim is a factual error in the end product: it
tells a reader that a duty has fallen away while it still holds. This tool measures how
often that happens, without an LLM and without the network -- both texts are on disk.

Every ``removed`` change of ``synopse.json`` is looked up in the full text of the new
edition (``neu/norm_doc.json``), normalised like the productive path (N2, lowercased),
so that whitespace, quote and dash variants cannot hide a hit. The verdict is
three-way, because the two failure modes have different causes and different owners:

=================  =====================================================  ==================
class              condition                                              broken stage
=================  =====================================================  ==================
``echt_entfallen`` the text appears nowhere in the new edition             nothing, correct
``falsch_positiv`` the text appears in the *mapped counterpart* chapter    paragraph alignment
``verschiebung``   the text appears in *another* chapter                   chapter alignment
=================  =====================================================  ==================

Short glossary/heading fragments (ADR-0002) are counted as ``kurzfragment`` and left out
of the verdict: they match anywhere by chance and would only add noise in both directions.

For every false positive the structural constellation is named as well
(:data:`CONSTELLATIONS`) -- that is what decides where a repair has to start.

The source directory is only ever read; both output files go to ``--out``, and
``regression.guard_write`` refuses any target below ``out/``.

Usage::

    python tools/removed_audit.py --dir out/4110_hot --out runs/AP-04_2026-08-10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- for regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.text.textnorm import compare_key, n2, split_sentences

#: Verdict classes, in report order.
CLASSES = ("echt_entfallen", "falsch_positiv", "verschiebung", "kurzfragment")

#: Structural constellations behind a false positive.
CONSTELLATIONS = {
    "verschmelzung_2_zu_1": "two old paragraphs become one new one; only the first is paired",
    "aufspaltung_1_zu_2": "one old paragraph is spread over several new ones",
    "satzumstellung": "the sentences survive in the chapter, but not as one block",
    "verpasste_paarung": "the host paragraph is itself reported as new -- no pairing at all",
    "doppelvorkommen": "an identical new paragraph exists but is paired with another old one",
    "sonstiges": "host paragraph outside the linked paragraphs of the record",
}

#: A removed span shorter than this (normalised characters) is treated as a fragment.
MIN_CHARS = 25
#: ... as is a span of at most this many words without a sentence end (ADR-0002 shape).
MAX_FRAGMENT_WORDS = 7

_SENTENCE_END = re.compile(r"[.!?][)\"']?\s*$")


def _norm(text: str | None) -> str:
    """Comparison form of the productive path: N2, lowercased.

    N2 collapses whitespace and unifies quotes and dashes, so a non-breaking hyphen or a
    doubled space cannot hide a hit. Case is folded as well -- a capitalisation
    difference is no removal either.
    """
    return n2(text or "").lower()


def is_fragment(text: str | None) -> bool:
    """True for spans too short to carry evidence either way (ADR-0002 shape)."""
    t = _norm(text)
    if len(t) < MIN_CHARS:
        return True
    return len(t.split()) <= MAX_FRAGMENT_WORDS and not _SENTENCE_END.search(t)


# -- the new edition as a searchable index ------------------------------------------------

def _index(new_doc: dict, norm=_norm) -> dict[str, dict]:
    """``section id -> {title, paras: [{id, text}], text}`` with normalised text."""
    index = {}
    for sec in new_doc.get("sections") or []:
        paras = [{"id": p.get("id"), "text": norm(p.get("n1") or p.get("n0"))}
                 for p in sec.get("paragraphs") or []]
        paras = [p for p in paras if p["text"]]
        index[sec.get("id")] = {"title": sec.get("title"), "paragraphs": paras,
                                "text": " ".join(p["text"] for p in paras)}
    return index


def _sentences(text: str, norm=_norm) -> list[str]:
    return [s for s in (norm(s) for s in split_sentences(n2(text or ""))) if s]


def _locate(needle: str, sentences: list[str], sec: dict) -> dict | None:
    """Where a removed span reappears inside one section, or ``None``.

    Two tiers: the span as one block (inside a single paragraph, or across paragraph
    boundaries), or -- failing that -- every one of its sentences somewhere in the
    section. The second tier catches sentences redistributed within a chapter; nothing
    is lost there either.
    """
    for p in sec["paragraphs"]:
        if needle in p["text"]:
            return {"treffer": "block", "absatz": p["id"]}
    if needle in sec["text"]:
        return {"treffer": "block", "absatz": None}
    if len(sentences) >= 2 and all(s in sec["text"] for s in sentences):
        return {"treffer": "saetze", "absatz": None}
    return None


# -- the structural constellation behind a false positive ---------------------------------

def _links_by_new_para(record: dict, mapping_record: dict | None) -> dict[str, dict]:
    """``new paragraph id -> the link that carries it``.

    ``mapping.json`` is the better source: it also holds the ``identical`` links, which
    ``synopse.json`` only counts. Without it the changes of the record are used.
    """
    links = (mapping_record or {}).get("para_links")
    if links is None:
        links = [{"old_ids": c.get("old_ids") or [], "new_ids": c.get("new_ids") or [],
                  "kind": c["kind"]} for c in record.get("changes") or []]
    out = {}
    for link in links:
        for nid in link.get("new_ids") or []:
            out[nid] = link
    return out


def _constellation(needle: str, hit: dict, sec: dict, links: dict[str, dict]) -> str:
    if hit["treffer"] == "saetze":
        return "satzumstellung"
    if hit["absatz"] is None:
        return "aufspaltung_1_zu_2"
    link = links.get(hit["absatz"])
    if link is None:
        return "sonstiges"
    if link["kind"] == "new" or not link.get("old_ids"):
        return "verpasste_paarung"
    host = next((p["text"] for p in sec["paragraphs"] if p["id"] == hit["absatz"]), "")
    if host == needle:
        return "doppelvorkommen"
    return "verschmelzung_2_zu_1"


# -- the audit ------------------------------------------------------------------------------

def _search(needle: str, sentences: list[str], index: dict[str, dict],
            counterparts: list[str]) -> tuple[str, str | None, dict | None]:
    """The verdict for one span: counterpart chapters first, then the whole edition."""
    for sid in counterparts:
        hit = _locate(needle, sentences, index[sid])
        if hit:
            return "falsch_positiv", sid, hit
    for sid, sec in index.items():
        if sid in counterparts:
            continue
        hit = _locate(needle, sentences, sec)
        if hit:
            return "verschiebung", sid, hit
    return "echt_entfallen", None, None


def audit(synopse: dict, new_doc: dict, mapping: dict | None = None) -> dict:
    """Classify every ``removed`` change of ``synopse`` against the new edition.

    Every span is looked up twice. The verdict follows the N2 form (the productive
    path). Spans that come out ``echt_entfallen`` are looked up a second time under
    ``compare_key`` -- the most aggressive form, spaces and punctuation removed. A hit
    only in the second pass means the text survived but was re-typeset (hyphenation,
    punctuation, PDF extraction). That number measures how much the strict verdict
    undercounts; it is reported separately and never changes the verdict.
    """
    index = _index(new_doc)
    index_ck = _index(new_doc, compare_key)
    by_old = {}
    for rec in (mapping or {}).get("records") or []:
        for oid in rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else []):
            by_old[oid] = rec

    findings: list[dict] = []
    counts = Counter()
    total = 0
    for ch in synopse.get("chapters") or []:
        chapter_id = ch.get("new_id") or ch.get("old_id")
        counterparts = [sid for sid in (ch.get("new_ids") or
                                        ([ch["new_id"]] if ch.get("new_id") else []))
                        if sid in index]
        mapping_record = by_old.get(ch.get("old_id")) if ch.get("old_id") else None
        links = _links_by_new_para(ch, mapping_record)

        for change in ch.get("changes") or []:
            if change["kind"] != "removed":
                continue
            total += 1
            text = change.get("old_text") or ""
            if is_fragment(text):
                counts["kurzfragment"] += 1
                continue
            needle = _norm(text)
            klasse, where, hit = _search(needle, _sentences(text), index, counterparts)
            klasse_ck = klasse
            if klasse == "echt_entfallen":
                klasse_ck = _search(compare_key(text), _sentences(text, compare_key),
                                    index_ck, counterparts)[0]
            counts[klasse] += 1
            finding = {
                "kapitel": chapter_id, "titel": ch.get("title"),
                "kapitel_modus": ch.get("mode"), "absatz_ids": change.get("old_ids") or [],
                "text": text, "zeichen": len(needle),
                "modalitaet": (change.get("modality") or {}).get("old"),
                "klasse": klasse, "klasse_compare_key": klasse_ck, "fundort": where,
                "fund_absatz": hit["absatz"] if hit else None,
                "treffer": hit["treffer"] if hit else None,
                "konstellation": (_constellation(needle, hit, index[where], links)
                                  if klasse == "falsch_positiv" else None),
            }
            findings.append(finding)

    constellations = Counter(f["konstellation"] for f in findings
                             if f["klasse"] == "falsch_positiv")
    duty = Counter(f["klasse"] for f in findings
                   if f["modalitaet"] and f["modalitaet"] != "informativ")
    lenient = Counter(f["klasse_compare_key"] for f in findings
                      if f["klasse"] == "echt_entfallen")
    return {"findings": findings, "total_removed": total,
            "counts": {k: counts.get(k, 0) for k in CLASSES},
            "konstellationen": dict(constellations.most_common()),
            "mit_pflicht": {k: duty.get(k, 0) for k in CLASSES},
            "nur_unter_compare_key": {k: lenient.get(k, 0)
                                      for k in ("falsch_positiv", "verschiebung")},
            "links_source": "mapping.json" if mapping else "synopse.json"}


def load(out_dir) -> tuple[dict, dict, dict | None]:
    """Read ``synopse.json``, ``neu/norm_doc.json`` and (if present) ``mapping.json``."""
    out = Path(out_dir)
    for name in ("synopse.json", "neu/norm_doc.json"):
        if not (out / name).exists():
            raise SystemExit(f"{out / name} is missing -- the audit needs the synopsis "
                             "and the new edition of a finished run.")
    mapping_path = out / "mapping.json"
    return (json.loads((out / "synopse.json").read_text(encoding="utf-8")),
            json.loads((out / "neu/norm_doc.json").read_text(encoding="utf-8")),
            json.loads(mapping_path.read_text(encoding="utf-8"))
            if mapping_path.exists() else None)


# -- report ---------------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:6.2f} %" if whole else "     -- "


def render(result: dict, src, digests: dict) -> str:
    total = result["total_removed"]
    counts, duty = result["counts"], result["mit_pflicht"]
    lines = [
        f"removed audit -- {src}",
        f"normpare {regression._version()}, links from {result['links_source']}",
        *(f"  {name:<22} {sha}" for name, sha in digests.items()),
        "",
        f"removed changes in the synopsis: {total}",
        "",
        "  class              count    share of all removed   thereof carrying a duty",
    ]
    for name in CLASSES:
        lines.append(f"  {name:<18} {counts[name]:>5}   {_pct(counts[name], total)}"
                     f"              {duty[name]:>5}")
    audited = total - counts["kurzfragment"]
    lenient = result["nur_unter_compare_key"]
    lines += [
        "",
        f"auditable spans (fragments excluded): {audited}",
        (f"  of those wrongly reported as removed: {counts['falsch_positiv']}"
         f" ({_pct(counts['falsch_positiv'], audited).strip()})"),
        "",
        "sensitivity -- spans found only under compare_key (spaces and punctuation",
        "removed), i.e. present but re-typeset. Counted, never part of the verdict:",
        f"  would be falsch_positiv  {lenient['falsch_positiv']:>5}",
        f"  would be verschiebung    {lenient['verschiebung']:>5}",
        "",
        "structural constellation of the false positives:",
    ]
    for name, n in result["konstellationen"].items():
        lines.append(f"  {name:<22} {n:>5}   {_pct(n, counts['falsch_positiv'])}"
                     f"   {CONSTELLATIONS.get(name, '')}")
    if not result["konstellationen"]:
        lines.append("  (none)")

    worst = sorted((f for f in result["findings"] if f["klasse"] == "falsch_positiv"),
                   key=lambda f: (-f["zeichen"], f["kapitel"] or "", f["absatz_ids"]))[:20]
    lines += ["", "the twenty longest false positives:"]
    for f in worst:
        lines += [
            (f"  chapter {f['kapitel']} ({f['titel']}) {f['absatz_ids']} "
             f"-> {f['fundort']}/{f['fund_absatz']} [{f['konstellation']}, {f['treffer']}, "
             f"modality {f['modalitaet']}]"),
            f"    {f['text']}",
        ]
    if not worst:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


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

    synopse, new_doc, mapping = load(src)
    result = audit(synopse, new_doc, mapping)
    digests = {n: regression.sha256_file(src / n)
               for n in ("synopse.json", "neu/norm_doc.json") if (src / n).exists()}

    payload = {"run": str(src), "normpare_version": regression._version(),
               "sources": digests,
               "summary": {k: v for k, v in result.items() if k != "findings"},
               "findings": result["findings"]}
    json_path = regression.guard_write(dest / f"removed_audit_{name}.json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    text = render(result, src, digests)
    txt_path = regression.guard_write(dest / f"removed_audit_{name}.txt")
    txt_path.write_text(text, encoding="utf-8")

    print(text)
    print(f"written: {json_path}\n         {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
