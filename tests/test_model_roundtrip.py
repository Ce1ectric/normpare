"""Round-trip tests for the domain model.

``tests/fixtures/norm_doc_sample.json`` is a synthetic document that covers every key and
asset variant the schema allows. ``from_dict(j).to_dict() == j`` is the schema-parity
guarantee: the typed model must never lose or reshape a field.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from normpare.model import NormDocument, Paragraph, Value

FIXTURE = Path(__file__).parent / "fixtures" / "norm_doc_sample.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_roundtrip_is_exact():
    data = _load()
    assert NormDocument.from_dict(data).to_dict() == data


def test_from_dict_does_not_mutate_source():
    data = _load()
    reference = copy.deepcopy(data)
    NormDocument.from_dict(data).to_dict()
    assert data == reference


def test_typed_access():
    nd = NormDocument.from_dict(_load())
    assert nd.doc_id
    for _section, paragraph in nd.iter_paragraphs():
        assert isinstance(paragraph, Paragraph)
        for value in paragraph.values:
            assert isinstance(value, Value)


def test_roundtrip_at_ingest_stage():
    """The model round-trips the ingest-stage schema too (before enrichment)."""
    ingest = {
        "doc_id": "X", "title": "T", "version_label": "v",
        "source": {"filename": "a.docx", "format": "docx"},
        "sections": [{
            "id": "vorspann", "title": "Vorspann", "level": 0, "part": "vorspann",
            "paragraphs": [
                {"id": "vorspann.p1", "n0": "Text", "kind": "text",
                 "tc": {"ins": 0, "del": 0}, "formulas": [], "figures": []},
                {"id": "vorspann.p2", "n0": "Begriff", "kind": "term",
                 "tc": {"ins": 1, "del": 0}, "formulas": ["X_eq_001"], "figures": [],
                 "term_no": "3.1.1"},
            ],
            "tables": [], "figures": [],
            "formulas": [{"id": "X_eq_001", "linear": "a=b", "latex": None,
                          "para_anchor": "vorspann.p2"}],
        }],
    }
    assert NormDocument.from_dict(ingest).to_dict() == ingest


def test_is_normative_matches_modality():
    nd = NormDocument.from_dict(_load())
    normative = [p for _, p in nd.iter_paragraphs() if p.is_normative()]
    informative = [p for _, p in nd.iter_paragraphs() if not p.is_normative()]
    assert normative and informative  # the fixture contains both kinds
    assert all(p.modality not in (None, "informativ") for p in normative)
