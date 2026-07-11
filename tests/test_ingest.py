"""Test the ingest dispatcher's error path (needs no lxml/PyMuPDF)."""
from __future__ import annotations

import pytest

from normpare.errors import IngestError
from normpare.stages.ingest import read_document


def test_read_document_rejects_unknown_format(tmp_path):
    with pytest.raises(IngestError):
        read_document(tmp_path / "source.txt", tmp_path, "id", "Title", "v1")
