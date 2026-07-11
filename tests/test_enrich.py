"""Functional test for the enrichment stage on a small synthetic ingest document."""
from __future__ import annotations

import json

from normpare.stages.enrich import enrich_doc


def test_enrich_adds_expected_fields(tmp_path):
    doc = {
        "doc_id": "X", "title": "T", "version_label": "v",
        "source": {"filename": "a.docx", "format": "docx"},
        "sections": [{
            "id": "1", "title": "Anwendungsbereich", "level": 1, "part": "hauptteil",
            "paragraphs": [
                {"id": "1.p1", "n0": "Die Anlage muss geerdet werden.", "kind": "text",
                 "tc": {"ins": 0, "del": 0}, "formulas": [], "figures": []},
                {"id": "1.p2", "n0": "Der Grenzwert liegt bei 500 kW nach VDE-AR-N 9999.",
                 "kind": "text", "tc": {"ins": 0, "del": 0}, "formulas": [], "figures": []},
            ],
            "tables": [], "figures": [], "formulas": [],
        }],
    }
    path = tmp_path / "norm_doc.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    enriched = enrich_doc(path)
    p1, p2 = enriched["sections"][0]["paragraphs"]
    assert p1["n1"] == "Die Anlage muss geerdet werden."
    assert p1["modality"] == "muss"
    assert any(v["raw"] == "500 kW" for v in p2["values"])
    assert "VDE-AR-N 9999" in p2["refs_external"]
    assert enriched["sections"][0]["modality_counts"].get("muss", 0) >= 1
