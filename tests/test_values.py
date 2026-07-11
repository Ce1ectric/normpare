"""Tests for normpare.stages.enrich.values (parameter extraction and diff)."""
from normpare.stages.enrich.values import extract_values, diff_values


def test_extract_basic():
    vs = extract_values("Die Statik beträgt 5 % bei 50,2 Hz.")
    raws = [v["raw"] for v in vs]
    assert "5 %" in raws
    assert "50,2 Hz" in raws


def test_base_unit_conversion():
    vs = extract_values("Dauer 100 ms")
    v = next(v for v in vs if v["unit"] == "ms")
    assert v["base_unit"] == "s"
    assert str(v["base_value"]) == "0.1"


def test_diff_changed():
    d = diff_values("Grenzwert 100 ms", "Grenzwert 200 ms")
    assert d["changed"], "value change must be detected"
    ch = d["changed"][0]
    assert ch["old"]["raw"] == "100 ms"
    assert ch["new"]["raw"] == "200 ms"


def test_diff_identical_no_change():
    d = diff_values("Grenzwert 100 ms", "Grenzwert 100 ms")
    assert d["changed"] == []


def test_german_number():
    vs = extract_values("Leistung 1.234,5 kW")
    assert any("1.234,5" in v["raw"] for v in vs)
