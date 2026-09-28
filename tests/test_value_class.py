"""AP-41 part C: a value change is not always a changed number.

Christian read all nine parameter-value changes of 4110 and all eight of 4120. Every one
of them is right in the text -- but only four resp. five of them change a number. The rest
add, drop or swap the operator in front of it: ``15 MVA -> mindestens 15 MVA`` is a
spelling, ``+- 5 % -> 5 %`` may well be a real narrowing.

So the four classes are **not** verdicts. They say where to look closely: a changed number
is always relevant, an added operator almost never. Nothing is dropped because of its
class -- the sum of the four is the number of entries.

The classification reads ``(base_value, base_unit, base_value2)`` of both sides and the
``op`` field. Nothing here interprets a standard; the example strings are inputs.
"""
from __future__ import annotations

from normpare.report.pptx import kennwert_groups
from normpare.stages.enrich.values import (
    OPERATOR_ADDED,
    OPERATOR_CHANGED,
    OPERATOR_REMOVED,
    VALUE_CHANGED,
    VALUE_CLASSES,
    diff_values,
    value_class,
)


def _single(old_text: str, new_text: str) -> dict:
    changed = diff_values(old_text, new_text)["changed"]
    assert len(changed) == 1, changed
    return changed[0]


def test_a_changed_number_is_value_changed():
    entry = _single("Die Leistung betraegt <= 950 kW.", "Die Leistung betraegt maximal 500 kW.")
    assert entry["value_class"] == VALUE_CHANGED
    assert value_class(entry["old"], entry["new"]) == VALUE_CHANGED
    # a changed number with the operator untouched is the same class
    assert _single("Es gelten 60 V.", "Es gelten 24 V.")["value_class"] == VALUE_CHANGED


def test_an_added_operator_is_operator_added():
    entry = _single("Die Anlage hat 15 MVA.", "Die Anlage hat mindestens 15 MVA.")
    assert entry["value_class"] == OPERATOR_ADDED
    assert _single("Die Abweichung betraegt 2 %.",
                   "Die Abweichung betraegt ± 2 %.")["value_class"] == OPERATOR_ADDED


def test_a_removed_operator_is_operator_removed():
    entry = _single("Die Abweichung betraegt ± 5 %.", "Die Abweichung betraegt 5 %.")
    assert entry["value_class"] == OPERATOR_REMOVED
    assert _single("Unterhalb < 47,5 Hz gilt dies.",
                   "Unterhalb 47,5 Hz gilt dies.")["value_class"] == OPERATOR_REMOVED


def test_a_switched_operator_is_operator_changed():
    entry = _single("Der Wert ist <= 5 %.", "Der Wert ist = 5 %.")
    assert entry["value_class"] == OPERATOR_CHANGED


def test_no_kennwert_change_is_dropped():
    """Every entry gets exactly one class, and every class reaches the slide."""
    pairs = [("Die Leistung betraegt <= 950 kW.", "Die Leistung betraegt maximal 500 kW."),
             ("Die Anlage hat 15 MVA.", "Die Anlage hat mindestens 15 MVA."),
             ("Die Abweichung betraegt ± 5 %.", "Die Abweichung betraegt 5 %."),
             ("Der Wert ist <= 5 %.", "Der Wert ist = 5 %.")]
    entries = [_single(o, n) for o, n in pairs]
    assert {e["value_class"] for e in entries} == set(VALUE_CLASSES)

    rows = [{"chapter": "11.2", "section": "11.2.6.7", "mapping_id": "m",
             "old": e["old"]["raw"], "new": e["new"]["raw"],
             "value_class": e["value_class"]} for e in entries]
    values, operators = kennwert_groups(rows)
    assert len(values) + len(operators) == len(rows)
    assert [r["value_class"] for r in values] == [VALUE_CHANGED]
    assert {r["value_class"] for r in operators} == {
        OPERATOR_ADDED, OPERATOR_REMOVED, OPERATOR_CHANGED}
    # a row without a class (a run written before AP-41) is not swallowed
    plain = [{"chapter": "5", "section": None, "old": "1 s", "new": "2 s"}]
    assert len(kennwert_groups(plain)[0]) == 1
