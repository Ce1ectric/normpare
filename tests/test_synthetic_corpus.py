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
