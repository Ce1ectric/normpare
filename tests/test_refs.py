"""Tests for normpare.stages.enrich.refs (internal + external references)."""
from normpare.stages.enrich.refs import internal, external


def test_internal():
    r = internal("Siehe Abschnitt 10.2.2.4 und Bild 5 sowie Tabelle 13.")
    assert "10.2.2.4" in r
    assert "Bild 5" in r
    assert "Tabelle 13" in r


def test_external():
    r = external("Nach DIN EN 12345 (VDE 0999-1) und VDE-AR-N 9999.")
    assert "DIN EN 12345" in r
    assert "VDE 0999-1" in r
    assert "VDE-AR-N 9999" in r


def test_empty():
    assert internal("Ohne jeden Verweis.") == []
