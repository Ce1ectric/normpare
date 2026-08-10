"""Tests for the evidence guard (``check_evidence``/``check_asset_evidence``).

Every case is synthetic: no test reads ``out/``, none needs real standard text and
none touches the network. The guard reports four quantities per interpretation:

``evidence_ok``
    the historical rule (15-character probe, short-quote branch) -- must stay
    bit-identical, which is what :func:`test_evidence_ok_parity_table` pins down.
``evidence_strict``
    every fragment of the quote occurs completely in the source text.
``evidence_match_chars``
    number of characters actually covered by the source text.
``evidence_fragments``
    number of fragments after splitting at ellipsis marks.
"""
from __future__ import annotations

import pytest

from normpare.stages.deutung import check_asset_evidence, check_evidence

# A source paragraph used by most cases. Lower case and single spaced, so that n2()
# leaves it untouched and character counts in the expectations are literal.
SOURCE = "die anforderungen an die messeinrichtung sind vom netzbetreiber festzulegen"


def _chapter(old_text: str = "", new_text: str = "") -> dict:
    """A chapter with exactly one change record, as ``check_evidence`` indexes it."""
    return {"changes": [{"old_text": old_text, "new_text": new_text}]}


def _check(evidence: str, old: str = "", new: str = SOURCE, index=0) -> dict:
    return check_evidence({"change_index": index, "evidence": evidence},
                          _chapter(old, new))


# -- 1: parity of the historical rule ---------------------------------------------------

# (label, old_text, new_text, change_index, evidence, expected evidence_ok)
# The expectations are derived from the *previous* implementation
# (``probe = ev[:60]``; ``return probe[:15] in hay or ev in hay``, with the
# short-quote branch ``ev == hay.strip()`` below 15 characters), not from the new one.
PARITY_TABLE = [
    ("empty quote", "", SOURCE, 0, "", False),
    ("whitespace only", "", SOURCE, 0, "   ", False),
    ("short quote equals the whole record", "", "kurz", 0, "kurz", True),
    ("short quote is only part of the record", "", "kurz und mehr", 0, "kurz", False),
    ("short quote, empty record", "", "", 0, "kurz", False),
    ("14 characters still take the short branch", "", "vierzehn zeich", 0,
     "vierzehn zeich", True),
    ("15 characters take the long branch", "", "fuenfzehn zeichen und mehr", 0,
     "fuenfzehn zeich", True),
    ("full quote present", "", SOURCE, 0,
     "die anforderungen an die messeinrichtung", True),
    ("only the first 15 characters present", "", SOURCE, 0,
     "die anforderungen an die schutzeinrichtung sind zu pruefen", True),
    ("nothing present", "", SOURCE, 0,
     "voellig anderer text mit vielen zeichen", False),
    ("quote found in the old text", SOURCE, "neuer text", 0,
     "die anforderungen an die messeinrichtung", True),
    ("quote spans old and new text", "alpha bravo charlie delta", "echo foxtrot", 0,
     "delta echo foxtrot", True),
    ("change_index out of range", "", SOURCE, 5,
     "die anforderungen an die messeinrichtung", False),
    ("change_index missing", "", SOURCE, None,
     "die anforderungen an die messeinrichtung", False),
    ("change_index is not an int", "", SOURCE, "0",
     "die anforderungen an die messeinrichtung", False),
    ("negative change_index", "", SOURCE, -1,
     "die anforderungen an die messeinrichtung", False),
    ("ellipsis: only the part before it is checked", "", SOURCE, 0,
     "die anforderungen … kommen im text nicht vor", True),
    ("a match beyond the 15-character probe does not help", "", SOURCE, 0,
     "xxxxxxxxxxxxxxx messeinrichtung sind vom netzbetreiber", False),
    ("quote is matched case-insensitively", "", SOURCE, 0,
     "DIE ANFORDERUNGEN AN DIE MESSEINRICHTUNG", True),
    ("whitespace in the quote is collapsed", "", SOURCE, 0,
     "die   anforderungen  an die", True),
    ("short quote is stripped before comparison", "", "kurz", 0, " kurz ", True),
]


@pytest.mark.parametrize("label,old,new,index,evidence,expected", PARITY_TABLE,
                         ids=[c[0] for c in PARITY_TABLE])
def test_evidence_ok_parity_table(label, old, new, index, evidence, expected):
    """``evidence_ok`` keeps the exact result of the historical rule in every case."""
    assert _check(evidence, old, new, index)["evidence_ok"] is expected


# -- 2..5: strict check and covered characters ------------------------------------------

def test_strict_false_when_only_prefix_matches():
    quote = "die anforderungen an die schutzeinrichtung sind zu pruefen"
    res = _check(quote)
    assert res["evidence_ok"] is True
    assert res["evidence_strict"] is False
    assert res["evidence_match_chars"] < len(quote)


def test_strict_true_when_full_quote_present():
    res = _check("die anforderungen an die messeinrichtung")
    assert res["evidence_ok"] is True
    assert res["evidence_strict"] is True


def test_match_chars_counts_verified_prefix():
    covered = "die anforderungen an die messeinrichtung"
    quote = covered + " sind einzuhalten"
    res = _check(quote, new=covered + ". weiteres steht hier.")
    assert res["evidence_match_chars"] == len(covered)
    assert res["evidence_match_chars"] < len(quote)


def test_match_chars_equals_length_when_strict():
    quote = "die anforderungen an die messeinrichtung"
    res = _check(quote)
    assert res["evidence_strict"] is True
    assert res["evidence_match_chars"] == len(quote)


# -- 6..9: ellipsis handling ------------------------------------------------------------

ELLIPSIS_SOURCE = "alpha bravo charlie und delta echo foxtrot"


def test_ellipsis_all_fragments_present():
    res = _check("alpha bravo … delta echo", new=ELLIPSIS_SOURCE)
    assert res["evidence_strict"] is True
    assert res["evidence_fragments"] == 2
    assert res["evidence_match_chars"] == len("alpha bravo") + len("delta echo")


def test_ellipsis_one_fragment_missing():
    res = _check("alpha bravo … zulu yankee", new=ELLIPSIS_SOURCE)
    assert res["evidence_strict"] is False
    assert res["evidence_fragments"] == 2


def test_ellipsis_variants_are_recognised():
    results = [_check(f"alpha bravo {mark} delta echo", new=ELLIPSIS_SOURCE)
               for mark in ("…", "...", "[…]", "[...]")]
    assert all(r == results[0] for r in results)
    assert results[0]["evidence_fragments"] == 2
    assert results[0]["evidence_strict"] is True


def test_empty_fragments_are_ignored():
    res = _check("alpha bravo … … delta echo", new=ELLIPSIS_SOURCE)
    assert res["evidence_fragments"] == 2


# -- 10..13: fail closed, unchanged branches --------------------------------------------

def test_empty_evidence_fails_closed():
    res = _check("")
    assert res["evidence_ok"] is False
    assert res["evidence_strict"] is False
    assert res["evidence_match_chars"] == 0
    assert res["evidence_fragments"] == 0


def test_invalid_change_index_fails_closed():
    res = _check("die anforderungen an die messeinrichtung", index=99)
    assert res["evidence_ok"] is False
    assert res["evidence_strict"] is False
    assert res["evidence_match_chars"] == 0


def test_short_quote_branch_unchanged():
    assert _check("kurz", new="kurz")["evidence_ok"] is True
    assert _check("kurz", new="kurz und mehr")["evidence_ok"] is False
    assert _check("kurz", new="")["evidence_ok"] is False


def test_ellipsis_does_not_affect_evidence_ok():
    accepted = ("die anforderungen an die messeinrichtung sind vom netzbetreiber",
                "die anforderungen an die … netzbetreiber festzulegen")
    rejected = ("voellig anderer text mit vielen zeichen",
                "voellig anderer … text mit vielen zeichen")
    for plain, with_ellipsis in (accepted, rejected):
        assert _check(plain)["evidence_ok"] is _check(with_ellipsis)["evidence_ok"]
    assert _check(accepted[0])["evidence_ok"] is True
    assert _check(rejected[0])["evidence_ok"] is False


# -- assets ------------------------------------------------------------------------------

ASSET_HAY = "tabelle 4 grenzwerte der spannung 110 kv und der frequenz"


def test_asset_evidence_reports_the_same_four_quantities():
    res = check_asset_evidence({"evidence": "grenzwerte der spannung"}, ASSET_HAY)
    assert res == {"evidence_ok": True, "evidence_strict": True,
                   "evidence_match_chars": len("grenzwerte der spannung"),
                   "evidence_fragments": 1}
    split = check_asset_evidence({"evidence": "grenzwerte … 110 kv"}, ASSET_HAY)
    assert split["evidence_fragments"] == 2
    assert split["evidence_strict"] is True
    short = check_asset_evidence({"evidence": "kurz"}, ASSET_HAY)
    assert short["evidence_ok"] is False
