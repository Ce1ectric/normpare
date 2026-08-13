"""A PDF line's text follows reading order, not drawing order.

PyMuPDF hands back the spans of a line in content-stream order, which is the order the
glyphs are drawn in, not the order they are read in. In DIN EN 60909-0:2016 the
subscript of ``i_p`` is drawn *before* its base glyph, so joining the spans as they
arrive yields ``"pi"`` -- and likewise ``"Gf R"`` for ``R_Gf`` or ``"kI"`` for ``I_k``.
Sorting the spans by x before joining restores the reading order.

The PDFs used here are written by the test itself -- PyMuPDF writes as well as it reads,
so no real standard and no fixture is involved.
"""
from __future__ import annotations

import fitz
import pytest

from normpare.stages.ingest import pdf
from normpare.stages.ingest.pdf import _collect_lines, read_pdf


def _write_pdf(path, pages) -> str:
    """Write a PDF. ``pages`` is a list of pages, each a list of (x, baseline_y, text,
    fontsize, bold) runs, appended in exactly that order -- the drawing order."""
    doc = fitz.open()
    for runs in pages:
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        for x, y, text, size, *rest in runs:
            bold = bool(rest and rest[0])
            writer.append((x, y), text, fontsize=size,
                          font=fitz.Font("hebo" if bold else "helv"))
        writer.write_text(page)
    doc.set_toc([])
    doc.save(str(path))
    doc.close()
    return str(path)


#: The subscript pair from DIN EN 60909-0:2016 p. 18: 'p' is drawn first at the larger x.
_SUBSCRIPT_RUNS = [(30, 100, "Berechnung von ", 10),
                   (109.5, 102, "p", 7),
                   (106, 100, "i", 10)]


def _mini_document(path) -> str:
    """Two numbered sections with a TOC, the second one carrying the subscript pair."""
    doc = fitz.open()
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((60, 90), "1 Anwendungsbereich", fontsize=14)
    writer.append((60, 120), "Diese Norm gilt fuer Drehstromnetze.", fontsize=10)
    writer.append((60, 134), "Sie gilt nicht fuer Gleichstrom.", fontsize=10)
    writer.write_text(page)
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((60, 90), "2 Berechnung", fontsize=14)
    writer.append((60, 120), "Diese Norm gilt fuer die Berechnung von ", fontsize=10)
    writer.append((255.9, 122), "p", fontsize=7)
    writer.append((252.4, 120), "i", fontsize=10)
    writer.append((60, 134), "in Drehstromnetzen.", fontsize=10)
    writer.write_text(page)
    doc.set_toc([[1, "1 Anwendungsbereich", 1], [1, "2 Berechnung", 2]])
    doc.save(str(path))
    doc.close()
    return str(path)


def _drawing_order_text(spans) -> str:
    """The behaviour before the fix: join the spans as PyMuPDF delivers them."""
    return "".join(s["text"] for s in spans)


def test_subscript_drawn_first_is_reordered(tmp_path):
    path = _write_pdf(tmp_path / "subscript.pdf", [_SUBSCRIPT_RUNS])
    with fitz.open(path) as doc:
        texts = [ln["text"] for ln in _collect_lines(doc)[0]]
    assert "Berechnung von ip" in texts
    assert "Berechnung von pi" not in texts


def test_already_ordered_spans_are_untouched(tmp_path):
    path = _write_pdf(tmp_path / "plain.pdf", [[
        (60, 100, "Diese Norm gilt fuer Drehstromnetze.", 10),
        (60, 130, "Sie gilt nicht fuer Gleichstrom.", 10)]])
    with fitz.open(path) as doc:
        texts = [ln["text"] for ln in _collect_lines(doc)[0]]
    assert texts == ["Diese Norm gilt fuer Drehstromnetze.",
                     "Sie gilt nicht fuer Gleichstrom."]


def test_equal_x_keeps_the_original_order():
    """Stable sort: spans at the same x stay in the order PyMuPDF delivered them."""
    spans = [{"text": "davor", "bbox": (40.0, 0.0, 60.0, 10.0)},
             {"text": "zweit", "bbox": (100.0, 0.0, 110.0, 10.0)},
             {"text": "erst", "bbox": (100.0, 0.0, 108.0, 10.0)}]
    assert pdf._line_text(spans) == "davorzweiterst"


def test_line_properties_are_unchanged(tmp_path):
    """``size``, ``bold`` and ``fonts`` keep being formed over *all* spans of a line."""
    path = _write_pdf(tmp_path / "props.pdf", [
        _SUBSCRIPT_RUNS + [(60, 140, "Fette Ueberschrift", 12, True)]])
    with fitz.open(path) as doc:
        raw = [ln for b in doc[0].get_text("dict")["blocks"] if b.get("type") == 0
               for ln in b["lines"] if "".join(s["text"] for s in ln["spans"]).strip()]
        lines = _collect_lines(doc)[0]
    assert len(lines) == len(raw)
    for got, ln in zip(lines, raw):
        assert got["size"] == max(s["size"] for s in ln["spans"])
        assert got["bold"] == any(s["flags"] & 16 for s in ln["spans"])
        assert got["fonts"] == {s["font"] for s in ln["spans"]}
    # the guard only bites if the page really mixes sizes and weights
    assert any(len({s["size"] for s in ln["spans"]}) > 1 for ln in raw)
    assert any(ln["bold"] for ln in lines)


def test_section_and_paragraph_counts_are_stable(tmp_path, monkeypatch):
    path = _mini_document(tmp_path / "mini.pdf")
    after = read_pdf(path, tmp_path / "after", "mini", "Mini", "alt")
    monkeypatch.setattr(pdf, "_line_text", _drawing_order_text)
    before = read_pdf(path, tmp_path / "before", "mini", "Mini", "alt")

    def shape(meta):
        return [(s["id"], s["title"], len(s["paragraphs"])) for s in meta["sections"]]

    assert shape(after) == shape(before)
    # ... and the reordering is the only thing that did change
    texts_after = [p["n0"] for s in after["sections"] for p in s["paragraphs"]]
    texts_before = [p["n0"] for s in before["sections"] for p in s["paragraphs"]]
    assert sum(a != b for a, b in zip(texts_after, texts_before)) == 1
    assert any(t.startswith("Diese Norm gilt fuer die Berechnung von ip")
               for t in texts_after)
    assert any(t.startswith("Diese Norm gilt fuer die Berechnung von pi")
               for t in texts_before)


def test_reading_is_deterministic(tmp_path):
    path = _mini_document(tmp_path / "mini.pdf")
    read_pdf(path, tmp_path / "first", "mini", "Mini", "alt")
    read_pdf(path, tmp_path / "second", "mini", "Mini", "alt")
    assert (tmp_path / "first" / "norm_doc.json").read_bytes() == \
           (tmp_path / "second" / "norm_doc.json").read_bytes()
