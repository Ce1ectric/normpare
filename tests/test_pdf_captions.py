"""A caption runs over several layout lines, and a lone number is a page number.

Three defects of the PDF paragraph reconstruction, measured in AP-39 over the three
frozen corpora and repaired here:

1. the caption branch took **one** line and left the rest of the caption in the body text
   ("Bild 21 - Schutzkonzept bei Anschluss von Erzeugungsanlagen an" + a paragraph
   "die Sammelschiene eines Umspannwerks"),
2. the separator between number and description was optional, so the tail of a sentence
   ("Tabelle 10 empfohlen.") was read as a caption and displaced the real one,
3. the digit filter only bit while a paragraph was open, so a page number between two
   paragraphs became a paragraph of its own.

The PDFs used here are written by the test itself -- PyMuPDF writes as well as it reads,
so no real standard and no fixture is involved.
"""
from __future__ import annotations

import fitz
import pytest

from normpare.stages.ingest import docx as docx_reader
from normpare.stages.ingest.pdf import _caption_line, _join_caption, read_pdf
from normpare.text.captions import is_caption_rest


# --------------------------------------------------------------------------- helpers

def _line(text: str, page: int = 1, y: float = 100.0, size: float = 10.0,
          bold: bool = False) -> dict:
    """One layout line as ``_collect_lines`` hands it over."""
    return {"page": page, "y": y, "x": 60.0, "x1": 400.0, "size": size, "bold": bold,
            "fonts": {"Helvetica"}, "text": text}


def _stack(*texts: str, page: int = 1, step: float = 12.0, y0: float = 100.0) -> list[dict]:
    """Lines below one another with a normal line spacing."""
    return [_line(t, page=page, y=y0 + i * step) for i, t in enumerate(texts)]


def _write_pdf(path, pages, toc) -> str:
    """Write a PDF. ``pages`` is a list of pages, each a list of runs:
    ``(x, y, text, size)`` for text, ``("image", x, y)`` for a 60x60 pixel image."""
    doc = fitz.open()
    for runs in pages:
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        wrote = False
        for run in runs:
            if run[0] == "image":
                _, x, y = run
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
                pix.set_rect(pix.irect, (200, 200, 200))
                page.insert_image(fitz.Rect(x, y, x + 120, y + 90), pixmap=pix)
                continue
            x, y, text, size = run
            writer.append((x, y), text, fontsize=size)
            wrote = True
        if wrote:
            writer.write_text(page)
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()
    return str(path)


def _texts(meta: dict) -> list[str]:
    return [p["n0"] for s in meta["sections"] for p in s["paragraphs"]]


# --------------------------------------------------------------- A: the caption is joined

def test_a_two_line_caption_is_joined():
    """The continuation line belongs to the caption, not to the body text."""
    lines = _stack("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an",
                   "die Sammelschiene eines Umspannwerks",
                   "Die Anforderungen an den Schutz sind einzuhalten.")
    text, nxt, capped = _join_caption(lines, 0)
    assert text == ("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an "
                    "die Sammelschiene eines Umspannwerks")
    assert nxt == 2 and capped is False


def test_a_caption_stops_at_a_sentence_end():
    """Ends the first line on a full stop, nothing is read on."""
    lines = _stack("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen.",
                   "Die Sammelschiene eines Umspannwerks ist zu beachten")
    text, nxt, capped = _join_caption(lines, 0)
    assert text == "Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen."
    assert nxt == 1 and capped is False


def test_a_caption_stops_at_a_large_gap():
    """A large line spacing ends the caption."""
    lines = [_line("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an", y=100.0),
             _line("die Sammelschiene eines Umspannwerks", y=140.0)]
    text, nxt, _ = _join_caption(lines, 0)
    assert text == "Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an"
    assert nxt == 1


def test_a_caption_stops_at_the_next_caption():
    """"Bild 22 - ..." ends the caption of Bild 21."""
    lines = _stack("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an",
                   "Bild 22 – Schutzkonzept bei Anschluss an die Sammelschiene")
    text, nxt, _ = _join_caption(lines, 0)
    assert text == "Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an"
    assert nxt == 1


def test_a_caption_is_capped_at_three_lines():
    """Beyond three lines the join is given up -- and the abort is counted."""
    lines = _stack("Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an",
                   "die Sammelschiene eines Umspannwerks sowie an",
                   "die Sammelschiene eines Kraftwerks sowie an",
                   "die Sammelschiene eines weiteren Umspannwerks")
    text, nxt, capped = _join_caption(lines, 0)
    assert capped is True
    assert nxt == 3
    assert text.endswith("eines Kraftwerks sowie an")
    assert "weiteren" not in text


def test_a_joined_caption_reaches_the_asset(tmp_path):
    """The asset carries the complete text, not the first line of it."""
    path = _write_pdf(
        tmp_path / "asset.pdf",
        [[(60, 90, "1 Schutzkonzept", 14),
          ("image", 60, 110),
          (60, 230, "Bild 21 – Schutzkonzept bei Anschluss von Erzeugungsanlagen an", 10),
          (60, 244, "die Sammelschiene eines Umspannwerks", 10)]],
        [[1, "1 Schutzkonzept", 1]])
    meta = read_pdf(path, tmp_path / "out", "asset", "Asset", "alt")
    figures = [f for s in meta["sections"] for f in s["figures"]]
    assert figures, "the test document must contain a figure"
    assert figures[0]["caption"] == ("Bild 21 – Schutzkonzept bei Anschluss von "
                                     "Erzeugungsanlagen an die Sammelschiene eines "
                                     "Umspannwerks")
    assert "die Sammelschiene eines Umspannwerks" not in _texts(meta)


# ------------------------------------------------------ B: the separator, with judgement

def test_a_sentence_end_is_not_a_caption(tmp_path):
    """"Tabelle 10 empfohlen." is the tail of a sentence and stays body text."""
    assert _caption_line("Tabelle 10 empfohlen.") is None
    assert is_caption_rest(" empfohlen.") is False
    path = _write_pdf(
        tmp_path / "sentence.pdf",
        [[(60, 90, "1 Einstellwerte", 14),
          (60, 120, "Als Grundparametrierung werden die Einstellwerte nach", 10),
          (60, 134, "Tabelle 10 empfohlen.", 10)]],
        [[1, "1 Einstellwerte", 1]])
    meta = read_pdf(path, tmp_path / "out", "sentence", "Sentence", "alt")
    assert any("Tabelle 10 empfohlen." in t for t in _texts(meta))


def test_a_real_caption_with_a_dash_is_one():
    """"Tabelle 10 - Empfohlene ..." keeps being a caption."""
    m = _caption_line("Tabelle 10 – Empfohlene Einstellwerte für den Schutz")
    assert m is not None and m.group(1) == "Tabelle"
    assert _caption_line("Bild 3c – Zweipoliger Kurzschluss") is not None
    assert _caption_line("Tabelle A.1: Kennwerte der Anlage") is not None


def test_a_continuation_page_caption_survives():
    """"Tabelle 1 (2 von 2)" is a real caption of a continuation page, without separator."""
    m = _caption_line("Tabelle 1 (2 von 2)")
    assert m is not None and m.group(1) == "Tabelle"
    assert is_caption_rest(" (2 von 2)") is True


# ------------------------------------------------------------- C: the page number falls

def test_a_page_number_between_paragraphs_is_dropped(tmp_path):
    """A digit line without an open paragraph falls, too -- it is the page number."""
    path = _write_pdf(
        tmp_path / "pageno.pdf",
        [[(60, 90, "1 Schutz", 14),
          (60, 120, "Der Schutz ist einzustellen.", 10),
          (60, 700, "116", 10)],
         [(60, 90, "2 Erdung", 14),
          (60, 120, "Die Erdung ist auszufuehren.", 10)]],
        [[1, "1 Schutz", 1], [1, "2 Erdung", 2]])
    meta = read_pdf(path, tmp_path / "out", "pageno", "Pageno", "alt")
    assert "116" not in _texts(meta)
    assert "Der Schutz ist einzustellen." in _texts(meta)


def test_a_page_number_inside_a_paragraph_still_drops(tmp_path):
    """The behaviour so far is unchanged: inside a running paragraph the number falls."""
    path = _write_pdf(
        tmp_path / "inside.pdf",
        [[(60, 90, "1 Schutz", 14),
          (60, 120, "Der Schutz ist nach den Vorgaben des", 10),
          (60, 700, "116", 10)],
         [(60, 120, "Netzbetreibers einzustellen.", 10)]],
        [[1, "1 Schutz", 1]])
    meta = read_pdf(path, tmp_path / "out", "inside", "Inside", "alt")
    assert "116" not in _texts(meta)
    assert any(t.startswith("Der Schutz ist nach den Vorgaben des Netzbetreibers")
               for t in _texts(meta))


def test_a_numeric_list_item_survives(tmp_path):
    """The boundary from part C: a column value in a legend is not a page number."""
    path = _write_pdf(
        tmp_path / "legend.pdf",
        [[(60, 90, "1 Legende", 14),
          (60, 120, "Legende", 10),
          (60, 140, "1", 10),
          (60, 160, "Netzanschlusspunkt", 10),
          (60, 300, "Die Bezeichnungen gelten fuer alle Bilder.", 10)]],
        [[1, "1 Legende", 1]])
    meta = read_pdf(path, tmp_path / "out", "legend", "Legend", "alt")
    assert "1" in _texts(meta)
    assert "Netzanschlusspunkt" in _texts(meta)


# ------------------------------------------------------------------ the DOCX is untouched

def test_the_docx_reader_is_untouched(tmp_path):
    """``docx.py`` is the control: it knows none of the three defects and keeps its rule."""
    docx = pytest.importorskip("docx")
    # (1) the DOCX reader keeps its own caption pattern and does not use the PDF rule
    assert docx_reader._CAPTION_RE.pattern == \
        r"^\s*(Tabelle|Bild|Abbildung)\s+([A-Z]?\.?\d+)\s*[–—\-:]?\s*(.*)"
    assert not hasattr(docx_reader, "_join_caption")
    assert not hasattr(docx_reader, "is_caption_rest")

    # (2) a digit paragraph survives the DOCX path, and reading it twice is byte-equal
    doc = docx.Document()
    doc.add_paragraph("Schutz", style="Heading 1")
    doc.add_paragraph("Der Schutz ist einzustellen.")
    doc.add_paragraph("116")
    doc.add_paragraph("Die Erdung ist auszufuehren.")
    src = tmp_path / "control.docx"
    doc.save(str(src))

    first = docx_reader.read_docx(src, tmp_path / "one", "ctl", "Control", "alt")
    docx_reader.read_docx(src, tmp_path / "two", "ctl", "Control", "alt")
    assert "116" in _texts(first)
    assert (tmp_path / "one" / "norm_doc.json").read_bytes() == \
           (tmp_path / "two" / "norm_doc.json").read_bytes()
