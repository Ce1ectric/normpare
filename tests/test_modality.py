"""Tests for normpare.stages.enrich.modality (deontic modality + shift)."""
from normpare.stages.enrich.modality import classify_sentence, classify_paragraph, shift


def test_classify_sentence():
    assert classify_sentence("Die Anlage muss geerdet werden.") == "muss"
    assert classify_sentence("Eine Wiederzuschaltung ist nicht zulässig.") == "darf_nicht"
    assert classify_sentence("Der Betreiber kann dies fordern.") == "kann"
    assert classify_sentence("Die Anlage sollte geprüft werden.") == "sollte"
    assert classify_sentence("ANMERKUNG Dies ist ein Hinweis.") == "informativ"


def test_classify_paragraph_max():
    p = classify_paragraph("Die Anlage muss geerdet werden. Sie kann zusätzlich geprüft werden.")
    assert p["max"] == "muss"
    assert p["counts"].get("muss") == 1
    assert p["counts"].get("kann") == 1


def test_shift():
    assert shift("kann", "muss") == "verschaerft"
    assert shift("muss", "kann") == "gelockert"
    assert shift("muss", "muss") == "unveraendert"
    assert shift("darf", "darf_nicht") == "verschaerft"
