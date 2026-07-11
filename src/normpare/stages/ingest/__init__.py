"""Ingest stage (S1): read a DOCX or PDF into a NormDocument.

The readers (``docx.py``, ``pdf.py``) turn a source document into a ``NormDocument``:
they write ``norm_doc.json`` (plus ``assets/`` and, for DOCX, ``track_changes.jsonl``)
exactly like v2 and return the meta dict. ``read_document`` dispatches by file extension
and wraps the result as a :class:`NormDocument`. The readers require ``python-docx``/``lxml``
(DOCX) or ``PyMuPDF`` (PDF); they are imported lazily so this module stays importable
without those dependencies.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import IngestError
from ...model import NormDocument


def read_document(path: str | Path, out_dir: str | Path, doc_id: str, title: str,
                  version_label: str) -> NormDocument:
    """Read ``path`` (DOCX or PDF) into a NormDocument, writing v2-compatible artifacts.

    Args:
        path: source document (``.docx`` or ``.pdf``).
        out_dir: directory for ``norm_doc.json`` and ``assets/``.
        doc_id, title, version_label: document metadata carried into ``norm_doc.json``.

    Raises:
        IngestError: if the file extension is neither ``.docx`` nor ``.pdf``.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from .docx import read_docx
        meta: Any = read_docx(path, out_dir, doc_id, title, version_label)
    elif suffix == ".pdf":
        from .pdf import read_pdf
        meta = read_pdf(path, out_dir, doc_id, title, version_label)
    else:
        raise IngestError(f"unsupported source format {suffix!r} (expected .docx or .pdf)")
    return NormDocument.from_dict(meta)
