"""The synthetic corpus TR-X 1000 against its specification.

``tests/fixtures/synthetic/erwartung.toml`` states which structural operation every
chapter mapping must yield. These tests read that file and check the deterministic
stages against it; the specification is never adjusted to the implementation.

The corpus contains no text from any real standard, so these tests run anywhere --
including CI, where the real standards must not go.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from normpare.text.textnorm import compare_key

_EXPECTATION = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"
     / "erwartung.toml").read_text(encoding="utf-8"))

#: Chapters the mapping cannot report as added today, with the reason and the
#: responsible package. The expectation stays as it is -- only the verdict is expected
#: to fail until the correspondence model can express it.
_KNOWN_GAPS = {
    "4.2": "AP-07 (correspondence graph): a new subchapter under a mapped parent is "
           "absorbed into the parent record (new_ids=['4', '4.2'], "
           "restructured='split_down') instead of getting a record of its own. Its "
           "text is reported as an added change inside chapter 4, so nothing is lost, "
           "but the chapter-level operation 'added' is not expressible in a 1:1 record.",
}


# -- helpers over the mapping records -----------------------------------------------------

def _record_by_old(mapping: dict, old_id: str) -> dict | None:
    """The record that starts from ``old_id`` -- also when it was absorbed into a parent."""
    for rec in mapping["records"]:
        if rec.get("old_id") == old_id or old_id in (rec.get("old_ids") or []):
            return rec
    return None


def _record_by_new(mapping: dict, new_id: str) -> dict | None:
    for rec in mapping["records"]:
        if rec.get("new_id") == new_id or new_id in (rec.get("new_ids") or []):
            return rec
    return None


def _chapter(synopse: dict, *, old_id: str | None = None, new_id: str | None = None) -> dict | None:
    for ch in synopse["chapters"]:
        if (old_id is not None and ch.get("old_id") == old_id) or \
           (new_id is not None and ch.get("new_id") == new_id):
            return ch
    return None


def _substantive(chapter: dict) -> list[dict]:
    """Changes that are not merely a reformulation -- what the interpretation stage sees."""
    return [c for c in (chapter or {}).get("changes") or [] if not c.get("semantic_equal")]


def _expected(expectation: dict, operation: str) -> list[dict]:
    return [z for z in expectation["zuordnung"] if z["operation"] == operation]


# -- 6: the specification itself ------------------------------------------------------------

def test_expectation_file_parses(expectation):
    """``erwartung.toml`` is readable and complete: corpus, mappings, modality, evidence."""
    assert expectation["korpus"]["alt"] == "2020-01"
    assert expectation["korpus"]["neu"] == "2026-01"

    operations = {z["operation"] for z in expectation["zuordnung"]}
    assert operations == {"unveraendert", "geaendert", "hinzugefuegt", "umnummeriert",
                          "zusammengefuehrt", "verschoben", "entfallen"}
    for z in expectation["zuordnung"]:
        assert z.get("alt") or z.get("neu"), "a mapping needs at least one side"
        assert z["richtung"], "every mapping carries a direction (axis C)"
    for m in expectation["modalitaet"]:
        assert m["erwartet"] in {"anforderung", "empfehlung", "verbot", "zulaessigkeit",
                                 "informativ"}
        assert m["text"] and m["fundstelle"]
    for e in expectation["evidenz"]:
        assert e["zitat"] and e["kapitel"]
        assert "erwartet_ok" in e


# -- 7..12: the structural mechanics ---------------------------------------------------------

def test_unchanged_chapters_report_no_change(expectation, synthetic_run):
    """The three control arms (1, 7, 9.1) must pass through silently."""
    unchanged = [z["alt"] for z in _expected(expectation, "unveraendert")]
    assert unchanged == ["1", "7", "9.1"]
    for sec_id in unchanged:
        rec = _record_by_old(synthetic_run.mapping, sec_id)
        assert rec is not None, f"chapter {sec_id} has no mapping record"
        assert rec.get("new_id") == sec_id or sec_id in (rec.get("new_ids") or [])
        assert _substantive(_chapter(synthetic_run.synopse, old_id=sec_id) or {}) == [], \
            f"chapter {sec_id} is identical in both editions but reports a change"


def test_renumbered_chapter_maps_to_new_number(expectation, synthetic_run):
    """Old 4.2 becomes new 4.3 -- mapping by number alone would land on the new 4.2."""
    spec = _expected(expectation, "umnummeriert")[0]
    assert (spec["alt"], spec["neu"]) == ("4.2", "4.3")
    rec = _record_by_old(synthetic_run.mapping, "4.2")
    assert rec is not None and rec.get("new_id") == "4.3"


def test_moved_chapter_is_found(expectation, synthetic_run):
    """Old 10 moves one level down and becomes new 9.2."""
    spec = _expected(expectation, "verschoben")[0]
    assert (spec["alt"], spec["neu"]) == ("10", "9.2")
    rec = _record_by_old(synthetic_run.mapping, "10")
    assert rec is not None
    assert rec.get("new_id") == "9.2" or "9.2" in (rec.get("new_ids") or [])


def test_merged_chapters_keep_both_sources(expectation, synthetic_run):
    """Old 6.1 and 6.2 both flow into new 6 -- neither source may be dropped."""
    spec = _expected(expectation, "zusammengefuehrt")[0]
    assert (spec["alt"], spec["neu"]) == (["6.1", "6.2"], "6")
    rec = _record_by_new(synthetic_run.mapping, "6")
    assert rec is not None
    sources = set(rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else []))
    assert {"6.1", "6.2"} <= sources, f"only these old chapters survived: {sorted(sources)}"


def test_removed_chapter_is_reported(expectation, synthetic_run):
    """Old 11 (reporting duty) is dropped without replacement."""
    spec = _expected(expectation, "entfallen")[0]
    assert spec["alt"] == "11"
    rec = _record_by_old(synthetic_run.mapping, "11")
    assert rec is not None
    assert rec["match_type"] == "removed" and rec.get("new_id") is None


@pytest.mark.parametrize("sec_id", sorted(z["neu"] for z in _EXPECTATION["zuordnung"]
                                          if z["operation"] == "hinzugefuegt"))
def test_added_chapters_are_reported(sec_id, request, expectation, synthetic_run):
    """New 4.2 and new 10 have no counterpart in the old edition."""
    assert sorted(z["neu"] for z in _expected(expectation, "hinzugefuegt")) == ["10", "4.2"]
    if sec_id in _KNOWN_GAPS:
        request.node.add_marker(pytest.mark.xfail(reason=_KNOWN_GAPS[sec_id], strict=True))
    rec = _record_by_new(synthetic_run.mapping, sec_id)
    assert rec is not None, f"new chapter {sec_id} appears in no record"
    assert rec["match_type"] == "new" and rec.get("old_id") is None, \
        f"new chapter {sec_id} is not reported as added but as {rec['match_type']}"


# -- 13..15: the paragraph level ------------------------------------------------------------
# Section ``[[absatz]]`` of the specification. The chapter-level expectations above pass
# while a sentence that survived verbatim is still reported as dropped -- the mapping is
# right, the paragraph alignment is not.

def _removed_changes(synopse: dict) -> list[tuple[dict, dict]]:
    return [(ch, c) for ch in synopse["chapters"] for c in ch["changes"]
            if c["kind"] == "removed"]


def test_merged_paragraph_is_not_reported_as_removed(expectation, synthetic_run):
    """The sentence stands verbatim in the new edition, so it must not be reported gone.

    Old 6.2 is a paragraph of its own; in the new edition the same sentence is the second
    sentence of a paragraph whose first sentence was already paired with old 6.1 (2:1
    merge). Claiming its removal asserts the end of a duty that still stands.
    """
    spec = expectation["absatz"][0]
    assert spec["fall"] == "verschmelzung_2_zu_1" and spec["erwartet"] == "unveraendert"
    wanted = compare_key(spec["satz"])
    offenders = [f"{ch.get('new_id') or ch.get('old_id')}: {c['old_ids']}"
                 for ch, c in _removed_changes(synthetic_run.synopse)
                 if wanted in compare_key(c.get("old_text") or "")]
    assert offenders == [], \
        f"{spec['satz']!r} is present in the new edition but reported as removed in {offenders}"


def test_merged_paragraph_keeps_both_sources(expectation, synthetic_run):
    """The new paragraph of chapter 6 carries both old paragraphs -- n:1 in ``para_links``.

    ``old_ids`` is a list, so the data model can express it (AP-00 F-3); what was missing
    is a pass that produces it.
    """
    spec = expectation["absatz"][0]
    wanted = compare_key(spec["satz"])
    host = next((p for s in synthetic_run.new_doc["sections"] if s["id"] == spec["neu"]
                 for p in s["paragraphs"]
                 if wanted in compare_key(p.get("n1") or p.get("n0") or "")), None)
    assert host is not None, f"new chapter {spec['neu']} does not contain the sentence at all"

    rec = _record_by_new(synthetic_run.mapping, spec["neu"])
    links = [l for l in rec.get("para_links") or [] if host["id"] in l["new_ids"]]
    assert len(links) == 1, f"the host paragraph appears in {len(links)} links, expected one"
    sources = set(links[0]["old_ids"])
    assert len(sources) >= 2, \
        (f"the merged paragraph {host['id']} carries only {sorted(sources)}; both old "
         f"paragraphs of {spec['alt']} flow into it")


def test_control_chapters_report_no_removals(expectation, synthetic_run):
    """The control arms (1, 7, 9.1) are identical in both editions -- nothing was dropped."""
    unchanged = [z["alt"] for z in _expected(expectation, "unveraendert")]
    for sec_id in unchanged:
        ch = _chapter(synthetic_run.synopse, old_id=sec_id) or {}
        removed = [c.get("old_text") for c in ch.get("changes") or []
                   if c["kind"] == "removed"]
        assert removed == [], f"control chapter {sec_id} reports {removed} as removed"


# -- 16: modality ---------------------------------------------------------------------------

def _contains_excerpt(text: str | None, excerpt: str) -> bool:
    """True when the words of ``excerpt`` appear in ``text``, in order.

    The excerpts in ``erwartung.toml`` name a construction ("muss gesichert sein"), not a
    contiguous span -- the sentence carries other words between them.
    """
    hay, pos = compare_key(text or ""), 0
    for token in excerpt.split():
        needle = compare_key(token)
        found = hay.find(needle, pos)
        if found < 0:
            return False
        pos = found + len(needle)
    return True


def test_modality_change_is_detected(expectation, synthetic_run):
    """Chapter 3 turns a recommendation into a requirement -- the change stream must say so.

    ``sollte gesichert sein`` -> ``muss gesichert sein`` is the one modality shift the
    corpus contains as a pair; both sides are listed in ``[[modalitaet]]``.
    """
    old_text = next(m["text"] for m in expectation["modalitaet"]
                    if m["fundstelle"] == "3 alt")
    new_text = next(m["text"] for m in expectation["modalitaet"]
                    if m["fundstelle"] == "3 neu")

    ch = _chapter(synthetic_run.synopse, new_id="3")
    assert ch is not None, "chapter 3 has no entry in the synopsis"
    hits = [c for c in ch.get("changes") or []
            if _contains_excerpt(c.get("new_text"), new_text)]
    assert len(hits) == 1, \
        f"{new_text!r} appears in {len(hits)} changes of chapter 3, expected one"

    change = hits[0]
    assert _contains_excerpt(change.get("old_text"), old_text), \
        "the change does not carry the old wording it replaces"
    assert (change.get("modality") or {}) == {"old": "sollte", "new": "muss",
                                              "shift": "verschaerft"}


# -- the interpretation stage joins back onto its chapters (AP-06) --------------------------

class _WrongIdProvider:
    """Answers every chapter, but names the same wrong ``section_id`` in all of them.

    That is what the real model did in the 4110 run: instead of echoing the chapter id
    from the prompt it wrote a slug of the heading or, for unnumbered annexes, the value
    of the ``Teil`` field -- ``anhang_informativ`` five times. 165 interpretations
    (10.6 %) could not be checked against their chapter afterwards. The stage must key
    the answer to its chapter itself instead of trusting the echo.
    """

    live = True

    def __init__(self, section_id: str = "anhang_informativ"):
        self.section_id = section_id

    def _answer(self, tag: str) -> dict:
        return {"section_id": self.section_id,
                "summary_old": "", "summary_new": "",
                "change_overview": f"overview of {tag}",
                "training_relevance": "low", "keywords": [], "practical_note": "",
                "interpretations": [
                    {"change_index": 0, "semantic_label": "clarified",
                     "obligation": "unchanged", "change": "a change",
                     "impact": "", "cross_reference_note": "", "evidence": "",
                     "confidence": "low", "contradiction_flag": False}]}

    def resolve(self, items: list[tuple[str, str]]) -> dict[str, dict]:
        return {tag: self._answer(tag) for tag, _ in items}


def test_every_interpretation_finds_its_chapter(synthetic_run, tmp_path):
    """Every interpretation still finds its chapter, even with a useless ``section_id``."""
    from normpare.report.dossier_report import build_dossier
    from normpare.stages.deutung import run_deutung

    deutung = run_deutung(synthetic_run.synopse, synthetic_run.old_doc,
                          synthetic_run.new_doc, tmp_path, tmp_path, model="fixture",
                          deutung_provider=_WrongIdProvider())
    dossier = build_dossier(synthetic_run.new_doc, synthetic_run.old_doc,
                            synthetic_run.synopse, deutung, tmp_path / "chapters.json")

    by_mapping = {c["mapping_id"]: c for c in dossier["chapters"]}
    assert len(by_mapping) == len(dossier["chapters"]), "chapters.json has a duplicate key"

    assert deutung["chapters"], "the corpus produced no interpretation at all"
    for d in deutung["chapters"]:
        ch = by_mapping.get(d.get("mapping_id"))
        assert ch is not None, f"interpretation {d.get('mapping_id')!r} has no chapter"
        assert ch["change_overview"] == d["change_overview"], \
            f"chapter {ch['mapping_id']} carries the interpretation of another chapter"


# -- reproducibility of the generated DOCX ------------------------------------------------

def test_generated_docx_is_byte_reproducible(tmp_path, load_tool, synthetic_dir):
    """Two builds from the same Markdown must yield identical files.

    A DOCX is a ZIP, and both the archive entries and the document's core properties
    carry timestamps. Without pinning them, every rebuild changes ``source.sha256`` in
    ``norm_doc.json`` and the tracked baseline goes red for no reason -- a harness that
    cries wolf stops being read.
    """
    import hashlib

    build = load_tool("build_synthetic")
    for name in ("alt", "neu"):
        erst = build.build_docx(synthetic_dir / f"{name}.md", tmp_path / "a" / f"{name}.docx")
        zweit = build.build_docx(synthetic_dir / f"{name}.md", tmp_path / "b" / f"{name}.docx")
        h1 = hashlib.sha256(erst.read_bytes()).hexdigest()
        h2 = hashlib.sha256(zweit.read_bytes()).hexdigest()
        assert h1 == h2, f"{name}.docx is not reproducible: {h1[:16]} != {h2[:16]}"


def test_generated_docx_carries_no_build_time(tmp_path, load_tool, synthetic_dir):
    """No archive entry may carry a timestamp other than the pinned one."""
    import zipfile

    build = load_tool("build_synthetic")
    pfad = build.build_docx(synthetic_dir / "alt.md", tmp_path / "alt.docx")
    with zipfile.ZipFile(pfad) as zf:
        zeiten = {info.date_time for info in zf.infolist()}
    assert zeiten == {(1980, 1, 1, 0, 0, 0)}, f"unpinned timestamps in the archive: {zeiten}"
