"""The caption reaches its asset, and the sub-heading is read to its end.

Two defects of :func:`normpare.stages.ingest.pdf.read_pdf`, both measured over the three
real corpora in AP-39a and repaired here:

* **B-1** -- tables and figures are collected per page and emitted as a *copy* at the
  first section that touches their page. A caption is only found in the paragraph pass
  and written into the original dictionary, so on a page that straddles a section
  boundary the copy has long left with ``caption: None`` and the caption never reaches
  the artefact. Measured before the change: 22 / 16 / 6 / 2 assets and 215 / 157 / 49 / 27
  words over the four PDF sources.
* **defect 4** -- the sub-heading branch took the rest of **one** line, so a wrapped
  sub-heading lost its continuation into an ordinary paragraph ("Gesamtübersicht zum
  Schutzkonzept bei Anschluss der Erzeugungsanlage an die" + a paragraph "Sammelschiene
  eines Umspannwerks"). Three cases, all in 4110.

The PDFs used here are written by the test itself -- PyMuPDF writes as well as it reads,
so no real standard and no fixture is involved.
"""
from __future__ import annotations

import logging

import fitz
import pytest

from normpare.stages.ingest import docx as docx_reader
from normpare.stages.ingest.pdf import MAX_CAPTION_LINES, read_pdf

# --------------------------------------------------------------------------- helpers


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


# --------------------------------------------------------------------------- documents


def _straddling_pdf(path) -> str:
    """A page that touches two sections, with the caption behind the boundary.

    Page 2 opens with the last line of section 1, carries a figure below it, and only
    then the heading of section 2 with the caption of that figure. So section 1 is the
    first section touching page 2 and owns the figure, while the caption is found in the
    paragraph pass of section 2 -- the constellation of 4110 B.1 (page 189) and B.8
    (page 197).
    """
    return _write_pdf(
        path,
        [[(60, 90, "1 Schutz", 14),
          (60, 130, "Der Schutz ist nach den Vorgaben des Netzbetreibers", 10)],
         [(60, 90, "einzustellen.", 10),
          ("image", 60, 120),
          (60, 300, "2 Erdung", 14),
          (60, 340, "Bild 1 – Netzanschlussschema der Kundenanlage", 10),
          (60, 380, "Die Erdung ist auszufuehren.", 10)]],
        [[1, "1 Schutz", 1], [1, "2 Erdung", 2]])


def _plain_pdf(path) -> str:
    """Two sections, each starting on its own page -- no page touches two sections."""
    return _write_pdf(
        path,
        [[(60, 90, "1 Schutz", 14),
          ("image", 60, 120),
          (60, 260, "Bild 1 – Schutzkonzept der Anlage", 10),
          (60, 300, "Der Schutz ist nach den Vorgaben einzustellen.", 10)],
         [(60, 90, "2 Erdung", 14),
          ("image", 60, 120),
          (60, 260, "Bild 2 – Erdungsschema der Anlage", 10),
          (60, 300, "Die Erdung ist auszufuehren.", 10)]],
        [[1, "1 Schutz", 1], [1, "2 Erdung", 2]])


def _assets(meta: dict, key: str = "figures") -> list[tuple[str, str]]:
    """(section id, asset id) for every emitted asset, in document order."""
    return [(s["id"], a["id"]) for s in meta["sections"] for a in s[key]]


# ------------------------------------------------- A: the asset waits for its caption

def test_a_caption_found_in_a_later_section_reaches_its_asset(tmp_path):
    """The page straddles the section boundary -- the caption still arrives."""
    meta = read_pdf(_straddling_pdf(tmp_path / "straddle.pdf"), tmp_path / "out",
                    "strad", "Straddle", "alt")
    figures = [f for s in meta["sections"] for f in s["figures"]]
    assert figures, "the test document must contain a figure"
    assert figures[0]["caption"] == "Bild 1 – Netzanschlussschema der Kundenanlage"
    # the caption was consumed by the caption branch, so it is not a paragraph either
    assert "Bild 1 – Netzanschlussschema der Kundenanlage" not in _texts(meta)


def test_an_asset_still_belongs_to_the_first_section_of_its_page(tmp_path):
    """The allocation is unchanged: the first section touching the page owns the asset."""
    meta = read_pdf(_straddling_pdf(tmp_path / "straddle.pdf"), tmp_path / "out",
                    "strad", "Straddle", "alt")
    assert [sid for sid, _ in _assets(meta)] == ["1"]
    assert [s["id"] for s in meta["sections"] if s["figures"]] == ["1"]


def test_an_asset_is_emitted_exactly_once(tmp_path):
    """``_used`` keeps its effect: no asset appears in two sections."""
    meta = read_pdf(_straddling_pdf(tmp_path / "straddle.pdf"), tmp_path / "out",
                    "strad", "Straddle", "alt")
    ids = [aid for _, aid in _assets(meta)]
    assert ids and len(ids) == len(set(ids))


def test_the_asset_order_within_a_section_is_unchanged(tmp_path):
    """Page order first, collection order within a page."""
    path = _write_pdf(
        tmp_path / "order.pdf",
        [[(60, 90, "1 Schutz", 14),
          ("image", 60, 120),
          ("image", 60, 260),
          (60, 400, "Der Schutz ist einzustellen.", 10)],
         [("image", 60, 120),
          (60, 300, "Die Erdung ist auszufuehren.", 10)]],
        [[1, "1 Schutz", 1]])
    meta = read_pdf(path, tmp_path / "out", "ord", "Order", "alt")
    figures = [f for s in meta["sections"] for f in s["figures"]]
    assert [f["id"] for f in figures] == ["ord_fig_001", "ord_fig_002", "ord_fig_003"]
    assert [f["page"] for f in figures] == [1, 1, 2]


def test_the_internal_keys_are_still_removed(tmp_path):
    """``y`` and ``_used`` are bookkeeping of the reader and stay out of the artefact."""
    meta = read_pdf(_straddling_pdf(tmp_path / "straddle.pdf"), tmp_path / "out",
                    "strad", "Straddle", "alt")
    assets = [a for s in meta["sections"] for key in ("tables", "figures") for a in s[key]]
    assert assets
    for a in assets:
        assert "y" not in a and "_used" not in a


def test_a_document_without_straddling_pages_is_unchanged(tmp_path):
    """No page touches two sections -- every asset keeps section, order and caption."""
    meta = read_pdf(_plain_pdf(tmp_path / "plain.pdf"), tmp_path / "out",
                    "plain", "Plain", "alt")
    assert _assets(meta) == [("1", "plain_fig_001"), ("2", "plain_fig_002")]
    captions = [a["caption"] for s in meta["sections"] for a in s["figures"]]
    assert captions == ["Bild 1 – Schutzkonzept der Anlage",
                        "Bild 2 – Erdungsschema der Anlage"]


# ------------------------------------------------ B: the sub-heading is read to its end

def test_a_wrapped_sub_heading_is_joined(tmp_path):
    """The continuation line belongs to the sub-heading, not to the body text."""
    path = _write_pdf(
        tmp_path / "subhead.pdf",
        [[(60, 90, "10.3 Schutz", 14),
          (60, 130, "10.3.4 Gesamtuebersicht zum Schutzkonzept bei Anschluss an die", 10),
          (60, 144, "Sammelschiene eines Umspannwerks", 10),
          (60, 200, "Das Schutzkonzept ist abzustimmen.", 10)]],
        [[1, "10.3 Schutz", 1]])
    meta = read_pdf(path, tmp_path / "out", "sub", "Sub", "alt")
    paras = [p for s in meta["sections"] for p in s["paragraphs"]]
    assert [(p["n0"], p["kind"]) for p in paras] == [
        (("Gesamtuebersicht zum Schutzkonzept bei Anschluss an die Sammelschiene "
          "eines Umspannwerks"), "term"),
        ("Das Schutzkonzept ist abzustimmen.", "text")]


def test_a_sub_heading_stops_at_a_sentence_end(tmp_path):
    """The same boundary as for the caption: a full stop ends the heading."""
    path = _write_pdf(
        tmp_path / "stop.pdf",
        [[(60, 90, "10.3 Schutz", 14),
          (60, 130, "10.3.4 Gesamtuebersicht zum Schutzkonzept.", 10),
          (60, 144, "Das Schutzkonzept ist abzustimmen.", 10)]],
        [[1, "10.3 Schutz", 1]])
    meta = read_pdf(path, tmp_path / "out", "stop", "Stop", "alt")
    paras = [p for s in meta["sections"] for p in s["paragraphs"]]
    assert [(p["n0"], p["kind"]) for p in paras] == [
        ("Gesamtuebersicht zum Schutzkonzept.", "term"),
        ("Das Schutzkonzept ist abzustimmen.", "text")]


def test_a_sub_heading_stops_at_a_large_gap(tmp_path):
    """A large line spacing ends the sub-heading, just as it ends a caption."""
    path = _write_pdf(
        tmp_path / "gap.pdf",
        [[(60, 90, "10.3 Schutz", 14),
          (60, 130, "10.3.4 Gesamtuebersicht zum Schutzkonzept bei Anschluss an die", 10),
          (60, 190, "Sammelschiene eines Umspannwerks", 10)]],
        [[1, "10.3 Schutz", 1]])
    meta = read_pdf(path, tmp_path / "out", "gap", "Gap", "alt")
    paras = [p for s in meta["sections"] for p in s["paragraphs"]]
    assert [(p["n0"], p["kind"]) for p in paras] == [
        ("Gesamtuebersicht zum Schutzkonzept bei Anschluss an die", "term"),
        ("Sammelschiene eines Umspannwerks", "text")]


def test_a_sub_heading_is_capped_and_counted(tmp_path, caplog):
    """Beyond the cap the join is given up -- and the abort is logged like a caption."""
    path = _write_pdf(
        tmp_path / "cap.pdf",
        [[(60, 90, "10.3 Schutz", 14),
          (60, 130, "10.3.4 Gesamtuebersicht zum Schutzkonzept bei Anschluss an die", 10),
          (60, 144, "Sammelschiene eines Umspannwerks sowie an", 10),
          (60, 158, "die Sammelschiene eines Kraftwerks sowie an", 10),
          (60, 172, "die Sammelschiene eines weiteren Umspannwerks", 10)]],
        [[1, "10.3 Schutz", 1]])
    with caplog.at_level(logging.INFO, logger="normpare.stages.ingest.pdf"):
        meta = read_pdf(path, tmp_path / "out", "cap", "Cap", "alt")
    texts = _texts(meta)
    assert texts[0] == ("Gesamtuebersicht zum Schutzkonzept bei Anschluss an die "
                        "Sammelschiene eines Umspannwerks sowie an "
                        "die Sammelschiene eines Kraftwerks sowie an")
    assert "weiteren" not in texts[0]
    assert "die Sammelschiene eines weiteren Umspannwerks" in texts
    assert any("sub-heading" in r.getMessage() and str(MAX_CAPTION_LINES) in r.getMessage()
               for r in caplog.records), caplog.text


def test_a_sub_heading_without_a_rest_is_unchanged(tmp_path):
    """The ``pending_term`` branch is untouched: the name on the next line stays alone."""
    path = _write_pdf(
        tmp_path / "pending.pdf",
        [[(60, 90, "3.1 Begriffe", 14),
          (60, 130, "3.1.11", 10),
          (60, 144, "voruebergehende Betriebserlaubnis", 10),
          (60, 158, "Erlaubnis, die vom Netzbetreiber erteilt wird", 10)]],
        [[1, "3.1 Begriffe", 1]])
    meta = read_pdf(path, tmp_path / "out", "pend", "Pending", "alt")
    paras = [p for s in meta["sections"] for p in s["paragraphs"]]
    assert [(p["n0"], p["kind"]) for p in paras] == [
        ("voruebergehende Betriebserlaubnis", "term"),
        ("Erlaubnis, die vom Netzbetreiber erteilt wird", "term")]


# ------------------------------------------------------------------ the DOCX is untouched

def test_the_docx_reader_is_untouched(tmp_path):
    """``docx.py`` is the control: it knows neither defect and its output is byte-equal."""
    docx = pytest.importorskip("docx")
    assert not hasattr(docx_reader, "_join_wrapped")
    assert not hasattr(docx_reader, "_join_sub_heading")

    doc = docx.Document()
    doc.add_paragraph("Schutz", style="Heading 1")
    doc.add_paragraph("Der Schutz ist einzustellen.")
    doc.add_paragraph("Erdung", style="Heading 1")
    doc.add_paragraph("Die Erdung ist auszufuehren.")
    src = tmp_path / "control.docx"
    doc.save(str(src))

    first = docx_reader.read_docx(src, tmp_path / "one", "ctl", "Control", "alt")
    docx_reader.read_docx(src, tmp_path / "two", "ctl", "Control", "alt")
    assert [s["id"] for s in first["sections"]] == ["vorspann", "1", "2"]
    assert (tmp_path / "one" / "norm_doc.json").read_bytes() == \
           (tmp_path / "two" / "norm_doc.json").read_bytes()
