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

#: Front-matter headings. They carry text but no chapter number, so they must not enter
#: the main-body counter -- otherwise every following chapter is shifted. Both readers
#: file them under ``vorspann.<name>``.
VORSPANN_TITLES = frozenset({"vorwort", "einleitung", "anwendungsbeginn", "änderungen"})

#: Headings of the navigation lists. They are not chapters and get no section at all.
SKIP_TITLES = frozenset({"inhalt", "inhaltsverzeichnis", "bilder", "tabellen",
                         "contents", "table of contents", "figures", "tables"})

# Both sets live here rather than in one reader because the DOCX and the PDF path have to
# agree on them. They did not: reading the 2023 edition as DOCX counted "Vorwort",
# "Einleitung", "Anwendungsbeginn" and "Änderungen" as chapters 1 to 4, so "Begriffe"
# came out as chapter 7 instead of 3 -- and every chapter number after it was wrong.


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
