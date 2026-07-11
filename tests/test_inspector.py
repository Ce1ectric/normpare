"""Test that the inspector renders a readable summary at every pipeline stage."""
from __future__ import annotations

import json
from pathlib import Path

from normpare.inspector import render_summary
from normpare.model import NormDocument

FIXTURE = Path(__file__).parent / "fixtures" / "norm_doc_sample.json"


def test_render_summary_contains_key_sections():
    nd = NormDocument.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    out = render_summary(nd)
    assert "Document :" in out
    assert "Sections :" in out
    assert "Modality :" in out
    assert "parameter value" in out


def test_render_summary_on_ingest_stage_document():
    # Before enrichment there are no values/modality/n1 -- the inspector must not crash.
    ingest = {
        "doc_id": "X", "title": "T", "version_label": "v",
        "source": {"filename": "a.docx", "format": "docx"},
        "sections": [{
            "id": "1", "title": "Anwendungsbereich", "level": 1, "part": "hauptteil",
            "paragraphs": [
                {"id": "1.p1", "n0": "Some text.", "kind": "text",
                 "tc": {"ins": 0, "del": 0}, "formulas": [], "figures": []},
            ],
            "tables": [], "figures": [], "formulas": [],
        }],
    }
    out = render_summary(NormDocument.from_dict(ingest))
    assert "Values   : 0" in out
    assert "Anwendungsbereich" in out
