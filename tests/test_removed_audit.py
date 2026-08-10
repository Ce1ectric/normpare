"""Tests for the removal audit (``tools/removed_audit.py``).

The audit answers one question per ``removed`` change of a finished run: is the text
really gone from the new edition, or is it still there and only lost by the alignment?
A wrong "dropped" claim is the most harmful error class of the whole product -- it
asserts that a duty has fallen away while it still stands.

Every test builds its own miniature run in memory. No test reads ``out/``, none needs
real standard text and none touches the network.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "removed_audit", Path(__file__).resolve().parents[1] / "tools" / "removed_audit.py")
removed_audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(removed_audit)


# -- miniature runs ---------------------------------------------------------------------

#: A full sentence, long enough to be audited, short enough to read in a failure message.
DUTY = "Der Betreiber bewahrt das Prüfbuch nach Anlage 3 zehn Jahre lang auf."


def _new_doc(*sections: tuple[str, str, list[str]]) -> dict:
    """``neu/norm_doc.json`` from ``(id, title, [paragraph texts])`` triples."""
    return {"doc_id": "new", "sections": [
        {"id": sid, "title": title, "level": 1,
         "paragraphs": [{"id": f"{sid}.p{i}", "kind": "text", "n1": text}
                        for i, text in enumerate(texts)]}
        for sid, title, texts in sections]}


def _synopse(chapter_id: str, removed: list[str], *,
             new_ids: list[str] | None = None, other: list[dict] | None = None) -> dict:
    """One chapter record whose ``removed`` changes carry the given old texts."""
    changes = [{"kind": "removed", "confidence": 0.0,
                "old_ids": [f"{chapter_id}.p{i}"], "new_ids": [],
                "old_text": text, "new_text": None}
               for i, text in enumerate(removed)]
    return {"pair": "test", "chapters": [{
        "old_id": chapter_id, "new_id": chapter_id,
        "old_ids": [chapter_id], "new_ids": new_ids or [chapter_id],
        "title": "Dokumentation", "level": 1, "mode": "id+title",
        "changes": changes + (other or []), "n_identical": 0}]}


def _only(result: dict) -> dict:
    """The single finding of a one-case run."""
    findings = result["findings"]
    assert len(findings) == 1, f"expected exactly one finding, got {findings}"
    return findings[0]


# -- 1..3: the three-way verdict ----------------------------------------------------------

def test_absent_text_is_a_genuine_removal():
    """Text that appears nowhere in the new edition is a correct ``removed`` report."""
    result = removed_audit.audit(
        _synopse("5", [DUTY]),
        _new_doc(("5", "Dokumentation", ["Die Prüfeinrichtung wird jährlich kalibriert."]),
                 ("8", "Nachweise", ["Der Nachweis wird dem Netzbetreiber vorgelegt."])))
    finding = _only(result)
    assert finding["klasse"] == "echt_entfallen"
    assert finding["fundort"] is None
    assert result["counts"]["echt_entfallen"] == 1


def test_text_in_counterpart_is_a_false_positive():
    """Text still present in the mapped counterpart chapter: the alignment lost it."""
    result = removed_audit.audit(
        _synopse("5", [DUTY]),
        _new_doc(("5", "Dokumentation",
                  ["Die Dokumentation ist vollständig zu führen. " + DUTY]),
                 ("8", "Nachweise", ["Der Nachweis wird dem Netzbetreiber vorgelegt."])))
    finding = _only(result)
    assert finding["klasse"] == "falsch_positiv", \
        "the sentence stands verbatim in the counterpart chapter"
    assert finding["fundort"] == "5"
    assert result["counts"]["falsch_positiv"] == 1


def test_text_in_another_chapter_is_a_move():
    """Text present in a *different* chapter: the chapter alignment lost it, not the
    paragraph alignment. Different cause, different responsibility."""
    result = removed_audit.audit(
        _synopse("5", [DUTY]),
        _new_doc(("5", "Dokumentation", ["Die Prüfeinrichtung wird jährlich kalibriert."]),
                 ("8", "Nachweise", ["Vorbemerkung. " + DUTY])))
    finding = _only(result)
    assert finding["klasse"] == "verschiebung"
    assert finding["fundort"] == "8"
    assert result["counts"]["verschiebung"] == 1


# -- 4: normalisation ---------------------------------------------------------------------

def test_normalisation_does_not_hide_a_match():
    """Whitespace and dash variants must not hide a hit -- the audit normalises like the
    productive path (N2), otherwise it would report a false positive as a real removal."""
    removed = "Die Messung nach IEC 60364-4 ist zu wiederholen."
    disguised = "Die   Messung\nnach IEC 60364‑4 ist  zu wiederholen."
    result = removed_audit.audit(
        _synopse("5", [removed]),
        _new_doc(("5", "Messung", ["Einleitung. " + disguised])))
    finding = _only(result)
    assert finding["klasse"] == "falsch_positiv", \
        "non-breaking hyphen and collapsed whitespace are not a textual change"


# -- 5: noise -----------------------------------------------------------------------------

def test_short_fragments_are_not_reported():
    """Short glossary/heading fragments (ADR-0002) are no evidence either way: they match
    anywhere by chance. They are counted, not reported."""
    result = removed_audit.audit(
        _synopse("3", ["Prüfbuch", "Anlage 3", DUTY]),
        _new_doc(("3", "Begriffe", ["Das Prüfbuch nach Anlage 3 ist zu führen."])))
    assert [f["text"] for f in result["findings"]] == [DUTY], \
        "only the full sentence is auditable; the two fragments are noise"
    assert result["counts"]["kurzfragment"] == 2
    assert result["total_removed"] == 3
