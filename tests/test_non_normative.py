"""Golden tests for ADR-0002: non-normative glossary/heading fragment classification.

Both directions matter: term/heading fragments are filtered, real short deletions are kept.
"""
from __future__ import annotations

from normpare.stages.diff import _is_non_normative


def test_term_name_fragment_is_filtered():
    assert _is_non_normative([{"kind": "term", "n1": "Flickerkoeffizient"}], "Begriffe")


def test_short_fragment_in_abbreviation_section_is_filtered():
    assert _is_non_normative([{"kind": "text", "n1": "Überschwingweite"}], "3.1 Begriffe")


def test_short_normative_sentence_is_kept():
    # normative statement in a normal chapter -> must NOT be filtered (recall protection)
    assert not _is_non_normative([{"kind": "text", "n1": "Der Schutz löst aus"}],
                                 "Schutzeinrichtungen")


def test_sentence_with_end_punctuation_is_kept():
    assert not _is_non_normative([{"kind": "term", "n1": "Der Wert ist maßgeblich."}], "Begriffe")


def test_fragment_with_parameter_value_is_kept():
    assert not _is_non_normative([{"kind": "text", "n1": "500 kV Grenzwert"}], "Begriffe")


def test_long_fragment_is_kept():
    assert not _is_non_normative(
        [{"kind": "term", "n1": "eins zwei drei vier fünf sechs sieben acht"}], "Begriffe")
