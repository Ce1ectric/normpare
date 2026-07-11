"""Test the data extraction for the final (LLM-interpreted) synopsis.

The interpretation JSON uses the neutral English schema; free-text values stay in the
document's language (here German).
"""
from __future__ import annotations

from normpare.report.synopse_final import _build_entries


def test_build_entries_bundles_and_skips_empty():
    synopse = {"chapters": [{"new_id": "5", "old_id": "5", "title": "Anschlussbedingungen"}]}
    deutung = {"language": "de", "chapters": [
        {
            "section_id": "5", "training_relevance": "high",
            "change_overview": "Neue Leistungsstaffelung.",
            "summary_new": "…", "practical_note": "Beispiel: 300 kW fällt unter 10.7.",
            "keywords": ["Anschluss/Anschlusskriterien"],
            "interpretations": [
                {"semantic_label": "new_obligation", "obligation": "tightened",
                 "change": "Grenzwert 500 kW eingeführt.", "impact": "Mehr Anlagen betroffen."},
                {"change": "", "impact": "wird übersprungen"},        # empty -> skipped
            ],
        },
        {"section_id": "9", "interpretations": []},  # no overview, no changes -> skipped
    ]}
    entries = _build_entries(synopse, deutung)
    assert len(entries) == 1
    e = entries[0]
    assert e["title"] == "Anschlussbedingungen"
    assert e["relevance"] == "high"
    assert e["overview"] == "Neue Leistungsstaffelung."
    assert e["keywords"] == ["Anschluss/Anschlusskriterien"]
    assert len(e["changes"]) == 1
    assert e["changes"][0]["label"] == "new_obligation"
    assert e["changes"][0]["binding"] == "tightened"
    assert e["changes"][0]["text"] == "Grenzwert 500 kW eingeführt."
    assert e["changes"][0]["impact"] == "Mehr Anlagen betroffen."


def test_enum_labels_render_in_the_document_language():
    from normpare.stages.deutung import label

    assert label("new_obligation", "de") == "neue Pflicht"
    assert label("tightened", "de") == "verschärft"
    assert label("high", "de") == "hoch"
    assert label("new_obligation", "en") == "new_obligation"   # identity for English
