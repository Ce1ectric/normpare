"""Test the keyword taxonomy assignment."""
from __future__ import annotations

from normpare.stages.keywords import TAXONOMY, assign_keywords


def test_assign_keywords_hits_erdung():
    doc = {"sections": [{
        "id": "3.1", "title": "Erdungsanlage", "part": "hauptteil",
        "paragraphs": [
            {"id": "3.1.p1", "n1": "Die Erdungsanlage und der Sternpunkt sind zu erden. "
                                   "Erdung, Erder und Potentialausgleich."},
        ],
    }]}
    kw_map = assign_keywords(doc)
    assert "Erdung/Sternpunktbehandlung" in TAXONOMY
    assert "Erdung/Sternpunktbehandlung" in doc["sections"][0]["keywords"]
    assert any(entry["id"] == "3.1" for entry in kw_map["Erdung/Sternpunktbehandlung"])
