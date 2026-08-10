"""Tests for normpare.stages.enrich.modality (deontic modality + shift)."""
from pathlib import Path

import pytest
import tomllib

from normpare.stages.enrich import modality
from normpare.stages.enrich.modality import classify_sentence, classify_paragraph, shift

_EXPECTATION = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"
     / "erwartung.toml").read_text(encoding="utf-8"))


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


# -- synthetic modal infinitives (AP-05) ---------------------------------------------------

def test_synthetic_infinitive_with_sein_is_a_requirement():
    """``sein`` + ``zu`` as an infix inside the verb is a duty, like ``muss``.

    "Die Einhaltung ist nachzuweisen." means the same as "Die Einhaltung muss
    nachgewiesen werden." -- German grammar, not an interpretation of the standard.
    """
    assert classify_sentence("Die Einhaltung der Grenzwerte ist nachzuweisen.") == "muss"
    assert classify_sentence("Die Grenzwerte sind einzuhalten.") == "muss"
    assert classify_sentence("Die Anlage ist unverzüglich abzuschalten.") == "muss"
    assert classify_sentence("Die Unterlagen sind zehn Jahre aufzubewahren.") == "muss"


def test_synthetic_infinitive_with_haben_is_a_requirement():
    """``haben`` + ``zu`` puts the duty on the acting party instead of on the object."""
    assert classify_sentence("Der Betreiber hat die Prüfung sicherzustellen.") == "muss"
    assert classify_sentence("Die Betreiber haben die Unterlagen vorzulegen.") == "muss"


def test_separable_prefix_variants():
    """Every separable prefix forms the infix the same way -- one pattern, not a list."""
    for sentence in ("Die Grenzwerte sind einzuhalten.",
                     "Die Aufzeichnungen sind aufzubewahren.",
                     "Der Nachweis ist vorzulegen.",
                     "Die Messung ist jährlich durchzuführen.",
                     "Die Werte sind im Datenblatt auszuweisen.",
                     "Das Verfahren ist mit dem Netzbetreiber abzustimmen."):
        assert classify_sentence(sentence) == "muss", sentence


def test_analytic_infinitive_still_recognised():
    """The analytic form was already covered and must stay covered."""
    assert classify_sentence("Die Anforderung ist zu erfüllen.") == "muss"
    assert classify_sentence("Die Prüfung ist vom Betreiber zu dokumentieren.") == "muss"
    assert classify_sentence("Die Prüfeinrichtung ist jährlich zu kalibrieren.") == "muss"


def test_synthetic_infinitive_ranks_like_muss():
    """Same rank, so ``shift()`` sees no movement between the two ways of saying it."""
    synthetic = classify_sentence("Die Einhaltung ist nachzuweisen.")
    assert modality.RANK[synthetic] == modality.RANK["muss"]
    assert shift("muss", synthetic) == "unveraendert"
    assert shift(synthetic, "sollte") == "gelockert"


# -- negation: prohibition vs. a duty that falls away --------------------------------------

def test_duerfen_negated_is_a_prohibition():
    """Negated permission is a prohibition -- the strongest binding class."""
    d = modality.deontic("Die Spannung darf 5 % nicht überschreiten.")
    assert d["label"] == "darf_nicht"
    assert (d["type"], d["polarity"]) == ("erlaubnis", "negativ")
    assert d["deontic_class"] == "verbot"


def test_muessen_negated_is_not_a_prohibition():
    """Negated necessity drops a duty; it does not forbid anything."""
    d = modality.deontic("Die Anlage muss nicht geprüft werden.")
    assert (d["type"], d["polarity"]) == ("pflicht", "negativ")
    assert d["deontic_class"] != "verbot"
    assert d["label"] != "darf_nicht"
    assert modality.RANK[d["label"]] < modality.RANK["muss"], \
        "a duty that falls away must not outrank a duty that stands"
    assert shift("muss", d["label"]) == "gelockert"


def test_brauchen_nicht_is_not_a_prohibition():
    """``braucht nicht`` and ``nicht erforderlich`` say the same as ``muss nicht``."""
    for sentence in ("Die Anlage braucht nicht geprüft zu werden.",
                     "Ein gesonderter Nachweis ist nicht erforderlich."):
        d = modality.deontic(sentence)
        assert (d["type"], d["polarity"]) == ("pflicht", "negativ"), sentence
        assert d["deontic_class"] != "verbot", sentence
        assert modality.RANK[d["label"]] < modality.RANK["muss"], sentence


# -- counter-checks: what must NOT trigger -------------------------------------------------

def test_descriptive_indicative_stays_informative():
    """Present indicative without a duty stays informative (the counter-case of AP-07)."""
    assert classify_sentence(
        "Die Auslegung richtet sich nach den Angaben des Herstellers.") == "informativ"
    assert classify_sentence("Die Spannung beträgt 20 kV.") == "informativ"


def test_noun_with_zu_is_not_a_requirement():
    r"""Nouns that merely carry the letters ``zu`` are not modal infinitives.

    ``Bezugsspannungen`` and ``Kurzunterbrechungen`` contain ``zu`` at a syllable
    boundary and end in ``-en``; a pattern of the shape ``\w+zu\w+en`` would read them
    as duties and turn a table caption into an obligation.
    """
    for sentence in ("Das Zubehör und die Zuleitung sind im Anhang aufgeführt.",
                     "Die Kurzunterbrechungen und die Bezugsspannungen sind in Tabelle 3 "
                     "angegeben.",
                     "Die Anzugsmomente sind dem Datenblatt beigefügt."):
        assert classify_sentence(sentence) == "informativ", sentence


def test_subordinate_clause_does_not_trigger():
    """A purpose clause states an aim, not a duty -- ``um ... zu`` must stay outside."""
    for sentence in ("Um die Grenzwerte einzuhalten, werden Filter eingesetzt.",
                     "Die Filter werden eingesetzt, um die Grenzwerte einzuhalten.",
                     "Der Betreiber prüft die Anlage, um Störungen zu vermeiden."):
        assert classify_sentence(sentence) == "informativ", sentence


# -- the corpus specification --------------------------------------------------------------

#: Cases of ``erwartung.toml`` whose construction is out of scope for AP-05, with the
#: reason. The expectation stays as it is; the case is expected to fail until the
#: construction is implemented. ``strict`` keeps it honest -- it flags the day the case
#: starts passing.
_OUT_OF_SCOPE = {
    "stellt sicher, dass":
        "present indicative carrying a duty -- deliberately outside AP-05 (part 4). "
        "Telling it apart from the descriptive indicative ('richtet sich nach den "
        "Angaben des Herstellers') is a package of its own.",
}


def _modality_cases():
    for case in _EXPECTATION["modalitaet"]:
        reason = _OUT_OF_SCOPE.get(case["text"])
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        yield pytest.param(case, marks=marks, id=case["text"])


@pytest.mark.parametrize("case", list(_modality_cases()))
def test_all_expectation_cases_from_the_corpus(case):
    """Every ``[[modalitaet]]`` entry of the corpus specification, in its own vocabulary."""
    assert modality.deontic(case["text"])["deontic_class"] == case["erwartet"], \
        f"{case['fundstelle']}: {case['konstruktion']}"
