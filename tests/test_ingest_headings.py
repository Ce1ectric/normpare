"""Unit tests for DOCX main-heading detection (German + English style names)."""
from __future__ import annotations

from normpare.stages.ingest.docx import _main_heading_level


def test_german_fnn_styles():
    assert _main_heading_level("berschrift1") == 1
    assert _main_heading_level("berschrift3") == 3


def test_english_heading_styles():
    assert _main_heading_level("Heading1") == 1
    assert _main_heading_level("heading 2") == 2
    assert _main_heading_level("Heading5") == 5


def test_non_headings_return_none():
    assert _main_heading_level("BodyText") is None
    assert _main_heading_level("TableParagraph") is None
    assert _main_heading_level("ListParagraph") is None
    assert _main_heading_level(None) is None
    assert _main_heading_level("(none)") is None
