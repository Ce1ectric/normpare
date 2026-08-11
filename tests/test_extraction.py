"""Tests for the quote extraction in front of the evidence guard (AP-08).

The guard used to check a model answer *as delivered*. A model that wraps its quote
in a label or in quotation marks failed the check even when it quoted correctly --
measured on ``out/4110_haiku``: 21.7 % passed as delivered, 79.9 % after the quoted
span was located. That is an error of the measurement, not of the model: it confounds
formatting discipline with the ability to cite. Quote-first [Gua26] puts the
localization of the span into the *system*.

The extractor draws candidate spans from an answer, in this order:

1. content between quotation marks -- several spans are possible, all are checked,
2. a leading label (``ALT:``, ``NEU:``, ``Neue Fassung:`` ...) removed,
3. the whole answer, which is what the guard did before.

The check passes when *one* candidate passes; ``evidence_span`` records the span it
passed with and ``evidence_extraction`` how that span was won (``roh``,
``anfuehrung``, ``etikett``). Everything here is synthetic: no run directory is read,
no network is touched.
"""
from __future__ import annotations

import pytest

from normpare.stages.deutung import check_evidence, evidence_candidates

#: The change record every case is checked against, already lower case and single
#: spaced so that ``n2()`` leaves it untouched and character counts are literal.
SOURCE = "die anforderungen an die messeinrichtung sind vom netzbetreiber festzulegen"

#: A span of the source, long enough to clear the 15-character floor.
QUOTE = "die anforderungen an die messeinrichtung"


def _chapter(*records: tuple[str, str]) -> dict:
    """A chapter whose change records are the given (old_text, new_text) pairs."""
    return {"changes": [{"old_text": o, "new_text": n} for o, n in records]}


def _check(answer: str, old: str = "", new: str = SOURCE, index: int = 0) -> dict:
    return check_evidence({"change_index": index, "evidence": answer},
                          _chapter((old, new)))


# -- 1: nothing changes for an answer without a shell --------------------------------

def test_bare_quote_is_unchanged():
    """A blank quote is its own candidate -- same verdict, same span, mode ``roh``."""
    passing = _check(QUOTE)
    assert passing["evidence_ok"] is True
    assert passing["evidence_strict"] is True
    assert passing["evidence_span"] == QUOTE
    assert passing["evidence_extraction"] == "roh"

    failing = _check("voellig anderer text mit vielen zeichen")
    assert failing["evidence_ok"] is False
    assert failing["evidence_extraction"] == "roh"


# -- 2: the quoted span is pulled out of the shell -----------------------------------

def test_quoted_span_is_extracted():
    """``NEU: '<quote>'`` passes on the quote, not on the shell."""
    answer = f"NEU: '{QUOTE}'"
    res = _check(answer)
    assert res["evidence_ok"] is True
    assert res["evidence_span"] == QUOTE
    # mode "anfuehrung" also says that the answer as delivered did *not* pass: the
    # whole answer is the first candidate, its 15-character probe reads "neu: 'die
    # anfo" and stands nowhere in the source text.
    assert res["evidence_extraction"] == "anfuehrung"
    assert evidence_candidates(answer)[0] == (answer, "roh")


# -- 3: all quotation mark pairs -----------------------------------------------------

#: The six pairs of the specification. ``n2()`` unifies the typographic ones before
#: the extractor sees them, only the single guillemets pass through unchanged.
QUOTE_PAIRS = ('"{}"', "'{}'", "‚{}‘", "„{}“",
               "»{}«", "›{}‹")


@pytest.mark.parametrize("pair", QUOTE_PAIRS, ids=[p.format("_") for p in QUOTE_PAIRS])
def test_all_quote_characters(pair):
    res = _check("NEU: " + pair.format(QUOTE))
    assert res["evidence_ok"] is True
    assert res["evidence_span"] == QUOTE
    assert res["evidence_extraction"] == "anfuehrung"


def test_mixed_quote_pair_is_extracted():
    """``‚quote'`` -- opening low-9, closing straight -- is what Haiku writes.

    300 of the 1612 answers of ``out/4110_haiku`` use this mismatched pair. It works
    because the extractor runs on the ``n2()``-normalized answer, where every
    typographic quotation mark has already become its straight equivalent.
    """
    res = _check("ALT: ‚" + QUOTE + "'")
    assert res["evidence_ok"] is True
    assert res["evidence_span"] == QUOTE


# -- 4: a label without quotation marks ----------------------------------------------

LABELS = ("ALT:", "NEU:", "Alte Fassung:", "Neue Fassung:", "Vorher:", "Nachher:",
          "NEU –", "Neue Fassung -")


@pytest.mark.parametrize("prefix", LABELS)
def test_label_without_quotes_is_stripped(prefix):
    res = _check(f"{prefix} {QUOTE}")
    assert res["evidence_ok"] is True
    assert res["evidence_span"] == QUOTE
    assert res["evidence_extraction"] == "etikett"


def test_hyphenated_word_is_not_a_label():
    """``Alt-Anlagen ...`` is a word, not a label -- the separator needs a space."""
    answer = "Alt-Anlagen sind hiervon nicht betroffen"
    assert [mode for _, mode in evidence_candidates(answer)] == ["roh"]


# -- 5: several spans, all of them checked -------------------------------------------

def test_multiple_spans_are_all_checked():
    """Two quotes in one answer: the check passes when the *second* one holds."""
    answer = f"ALT: 'ein satz der so nirgends steht'; NEU: '{QUOTE}'"
    res = _check(answer)
    assert res["evidence_ok"] is True
    assert res["evidence_span"] == QUOTE
    assert res["evidence_extraction"] == "anfuehrung"
    spans = [c for c, mode in evidence_candidates(answer) if mode == "anfuehrung"]
    assert spans == ["ein satz der so nirgends steht", QUOTE]


# -- 6: the 15-character floor -------------------------------------------------------

def test_span_shorter_than_fifteen_is_not_a_candidate():
    """A single quoted word must not become a candidate.

    Without the floor this answer would pass: the record is the word itself, and the
    short-quote branch of ``evidence_ok`` accepts a quote that covers the whole
    record. Every model could then pass by quoting an arbitrary word.
    """
    answer = 'Der Begriff "Netz" wurde ergaenzt'
    res = _check(answer, new="netz")
    assert res["evidence_ok"] is False
    assert res["evidence_extraction"] == "roh"
    assert [mode for _, mode in evidence_candidates(answer)] == ["roh"]


# -- 7: the mode is recorded ---------------------------------------------------------

def test_extraction_mode_is_recorded():
    assert _check(QUOTE)["evidence_extraction"] == "roh"
    assert _check(f"NEU: '{QUOTE}'")["evidence_extraction"] == "anfuehrung"
    assert _check(f"Neue Fassung: {QUOTE}")["evidence_extraction"] == "etikett"


# -- 9: the extracted span feeds every evidence field --------------------------------

def test_extraction_feeds_all_evidence_fields():
    """``strict``, ``match_chars`` and ``unique`` are computed on the span.

    Not on the answer: the shell is not part of the quote, so its characters must
    neither count as covered nor as missing.
    """
    other = "die pruefung erfolgt nach den vorgaben des netzbetreibers"
    chapter = _chapter(("", SOURCE), ("", other))
    res = check_evidence({"change_index": 0, "evidence": f"NEU: '{QUOTE}'"}, chapter)
    assert res["evidence_strict"] is True
    assert res["evidence_match_chars"] == len(QUOTE)
    assert res["evidence_fragments"] == 1
    assert res["evidence_unique"] is True

    # the same quote with an ellipsis: the split happens on the span, too
    split = _check("NEU: 'die anforderungen … netzbetreiber festzulegen'")
    assert split["evidence_fragments"] == 2
    assert split["evidence_strict"] is True


def test_extraction_does_not_invent_a_match():
    """A quoted span that stands nowhere fails -- extraction rescues form, not content."""
    res = _check("NEU: 'ein vollstaendig erfundener satz ueber messwerte'")
    assert res["evidence_ok"] is False
    assert res["evidence_strict"] is False


# -- 10: nested quotation marks ------------------------------------------------------

def test_nested_quotes_do_not_break():
    """A quote inside a quote is extracted as one span and does not raise."""
    inner = "der begriff ‚messeinrichtung' ist neu"
    res = _check(f'NEU: "{inner}"', new=inner)
    assert res["evidence_ok"] is True
    assert res["evidence_strict"] is True
    assert res["evidence_span"] == "der begriff 'messeinrichtung' ist neu"

    # an unbalanced mark leaves no quoted span: the label branch takes over and the
    # answer is checked with its stray mark, but nothing raises
    assert [m for _, m in evidence_candidates(f'NEU: "{QUOTE}')] == ["roh", "etikett"]


# -- 11: fail closed -----------------------------------------------------------------

def test_empty_answer_fails_closed():
    res = _check("")
    assert res["evidence_ok"] is False
    assert res["evidence_strict"] is False
    assert res["evidence_unique"] is False
    assert res["evidence_match_chars"] == 0
    assert res["evidence_fragments"] == 0
    assert res["evidence_span"] == ""
    assert res["evidence_extraction"] == "roh"

    empty_shell = _check("NEU: ''")
    assert empty_shell["evidence_ok"] is False
    assert empty_shell["evidence_extraction"] == "roh"
