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
``evidence_unique``
    strict in the addressed change record and in no other record of the chapter
    (ENT-30) -- a marker, not a gate.
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


# -- evidence_unique (ENT-30) --------------------------------------------------------------

OTHER = "die pruefung erfolgt nach den vorgaben des netzbetreibers"


def _two_records(first_new: str, second_new: str) -> dict:
    return {"changes": [{"old_text": "", "new_text": first_new},
                        {"old_text": "", "new_text": second_new}]}


def _check_in(chapter: dict, evidence: str, index: int = 0) -> dict:
    return check_evidence({"change_index": index, "evidence": evidence}, chapter)


def test_evidence_unique_true_for_a_single_match():
    """The quote is verbatim in the addressed record and nowhere else in the chapter."""
    res = _check_in(_two_records(SOURCE, OTHER), "die anforderungen an die messeinrichtung")
    assert res["evidence_strict"] is True
    assert res["evidence_unique"] is True


def test_evidence_unique_false_when_another_record_matches():
    """The same quote also fits a foreign record -- the interpretation is not localizable.

    This is the upper bound for undetectable ``change_index`` errors (ENT-30): the
    interpretation may address the wrong record without anyone noticing.
    """
    res = _check_in(_two_records(SOURCE, SOURCE + " und weiteres"),
                    "die anforderungen an die messeinrichtung")
    assert res["evidence_strict"] is True
    assert res["evidence_unique"] is False


def test_evidence_unique_requires_strict():
    """Without a complete match in the addressed record there is nothing unique to report."""
    res = _check_in(_two_records(SOURCE, OTHER),
                    "die anforderungen an die schutzeinrichtung sind zu pruefen")
    assert res["evidence_ok"] is True
    assert res["evidence_strict"] is False
    assert res["evidence_unique"] is False


# -- the four evidence cases of the synthetic corpus ----------------------------------------

def _addressed_record(chapter: dict, quote: str) -> tuple[int, dict]:
    """Index of the record the quote covers best, plus the result of the guard there.

    ``erwartung.toml`` names the chapter, not the change record; the record with the
    most covered characters is the one the quote is about (ties: the first).
    """
    results = [check_evidence({"change_index": i, "evidence": quote}, chapter)
               for i in range(len(chapter["changes"]))]
    best = max(range(len(results)), key=lambda i: results[i]["evidence_match_chars"])
    return best, results[best]


def test_synthetic_evidence_cases(expectation, synthetic_run):
    """The four evidence cases of ``erwartung.toml`` over the real change records."""
    by_new = {ch.get("new_id"): ch for ch in synthetic_run.synopse["chapters"]}
    failures = []
    for case in expectation["evidenz"]:
        chapter = by_new.get(case["kapitel"])
        assert chapter is not None, f"chapter {case['kapitel']} is missing from the synopsis"
        index, res = _addressed_record(chapter, case["zitat"])
        got = {k: res[k] for k in ("evidence_ok", "evidence_strict", "evidence_fragments",
                                   "evidence_unique")}
        want = {"evidence_ok": case["erwartet_ok"]}
        for key, field in (("erwartet_strict", "evidence_strict"),
                           ("erwartet_fragmente", "evidence_fragments"),
                           ("erwartet_unique", "evidence_unique")):
            if key in case:
                want[field] = case[key]
        if any(got[k] != v for k, v in want.items()):
            failures.append(f"{case['fall']} (chapter {case['kapitel']}, record {index}): "
                            f"expected {want}, got {got}")
    assert not failures, "\n".join(failures)
