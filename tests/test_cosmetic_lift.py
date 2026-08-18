"""AP-24, part 1: a ``cosmetic`` verdict must not hide a substantive change.

Three signals lift the verdict -- a changed parameter value, a changed set of qualifier
words, a changed reference. The naive "some digit differs" rule is deliberately *not*
implemented (it would hit 53 of 75 cosmetic changes at 4110: standard numbers, years,
cross-references), so the two counter-tests below have to stay cosmetic.

All texts are synthetic; no standard text, no network, no LLM.
"""
from __future__ import annotations

from normpare.stages.diff import _para_change
from normpare.stages.enrich import refs


def _para(pid: str, text: str, *, refs_int=None, refs_ext=None) -> dict:
    """A paragraph as the enrich stage leaves it (references enriched from the text
    unless the test pins them, which it does where the reference set is unchanged)."""
    return {"id": pid, "kind": "text", "n1": text, "modality": "muss",
            "refs_internal": refs.internal(text) if refs_int is None else refs_int,
            "refs_external": refs.external(text) if refs_ext is None else refs_ext}


def _change(old: str, new: str, **kw) -> dict:
    return _para_change("similar", [_para("o1", old, **kw)], [_para("n1", new, **kw)], 0.9)


def test_a_changed_limit_is_not_cosmetic():
    rec = _change("Die Erkennungsschwelle beträgt mindestens 5 %.",
                  "Die Erkennungsschwelle beträgt mindestens 1 %.")
    assert rec["kennwerte"]["changed"], "precondition: the value diff sees the change"
    assert rec["kind"] == "similar"


def test_a_dropped_qualifier_is_not_cosmetic():
    rec = _change("Die Auslegung erfolgt ggf. unter Berücksichtigung der Netzimpedanz.",
                  "Die Auslegung erfolgt unter Berücksichtigung der Netzimpedanz.")
    assert not rec["kennwerte"]["changed"], "precondition: no value carries this case"
    assert rec["kind"] == "similar"


def test_a_changed_reference_is_not_cosmetic():
    rec = _change("Die Anforderungen nach Abschnitt 10.2.2.4 sind einzuhalten.",
                  "Die Anforderungen nach Abschnitt 10.2.2 sind einzuhalten.")
    assert rec["refs"]["internal_removed"] == ["10.2.2.4"], "precondition: refs see it"
    assert rec["kind"] == "similar"


def test_a_norm_number_stays_cosmetic():
    # The digits of a standard number differ, nothing else. Under the naive digit rule this
    # would be lifted; here the reference set is unchanged (both sides cite the same
    # standard), no value and no qualifier moves -- so it stays cosmetic.
    rec = _change("Es gilt DIN EN 50380 für die Kennzeichnung der Betriebsmittel.",
                  "Es gilt DIN EN 50380 (VDE 0126) für die Kennzeichnung der Betriebsmittel.",
                  refs_ext=["DIN EN 50380"])
    assert rec["kind"] == "cosmetic"
    assert rec["semantic_equal"] is True


def test_pure_formatting_stays_cosmetic():
    rec = _change("Der Wert beträgt 2 % der Bemessungsleistung.",
                  "Der Wert beträgt 2,0 % der Bemessungsleistung.")
    assert not rec["kennwerte"]["changed"], "2 and 2,0 are the same value"
    assert rec["kind"] == "cosmetic"
    assert rec["semantic_equal"] is True


def test_semantic_equal_is_not_set_when_lifted():
    rec = _change("Die Erkennungsschwelle beträgt mindestens 5 %.",
                  "Die Erkennungsschwelle beträgt mindestens 1 %.")
    assert rec.get("semantic_equal") is not True
