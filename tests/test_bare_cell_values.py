"""Values without a unit in a table cell (AP-34).

In a table the unit stands in the column heading and the cell carries the bare number:
``Dämpfungsmaß`` over ``≥ 0,06``. ``extract_values`` demands a unit, so exactly those
limit values were invisible -- of 97 statements in ``value_changes`` that AP-31 could
join to a table, only 45 carried a value the check could look for.

The package widens the detection **for cells only**, and only to numbers with a decimal
place (variant B of the decision gate): the cell carries its context in the column
heading, running text does not, where "0,06" without a unit is usually a chapter number.
Measured over the three reference runs before a line was written: variant B adds 89
values to the check, of which every single one is a limit value and none is a running
number -- while variant A ("any bare number") would have added 1115 one- and two-digit
integers at 4110 alone (``runs/AP-34_2026-09-01/messung.md``).

A value without a unit carries ``base_unit: None`` and therefore pairs only with another
unitless value, never with "0,06 s".

Part B repairs a separate, older defect of the same function: ``extract_values("1 000 ms")``
read ``000 ms`` = 0. That was silently **wrong**, not missing, and it is the one change of
this package that also touches paragraphs.

Synthetic material throughout, no standard, no network.
"""
from __future__ import annotations

import inspect
import json

from normpare.report.stats import build_statistics
from normpare.stages.deutung import check_asset_values, resolve_table
from normpare.stages.enrich.values import (
    cell_values,
    extract_cell_values,
    extract_values,
    value_keys,
)

# -- the two ends of the check ----------------------------------------------------------

#: A paragraph without any thousands separator: what it yields must not move at all.
PARAGRAPH = ("Das Dämpfungsmaß der Regelung beträgt 0,06 und die Statik 5 %; "
             "nach Abschnitt 10.2.5 gilt eine Anregelzeit von 1.000 ms.")

#: What ``extract_values`` returned for it before AP-34, character for character.
PARAGRAPH_VALUES = [
    {"op": None, "value": "5", "unit": "%", "base_value": "5", "base_unit": "%",
     "raw": "5 %"},
    {"op": None, "value": "1000", "unit": "ms", "base_value": "1", "base_unit": "s",
     "raw": "1.000 ms"},
]


def test_a_bare_decimal_in_a_cell_is_a_value():
    """``1,10`` in a cell is the voltage factor c, and it is a value.

    It is the form a limit value takes in a table: the number in the cell, the unit in
    the column heading. Marked as unitless, so it stays distinguishable from "1,10 kV".
    """
    found = extract_cell_values("1,10")
    assert len(found) == 1
    assert found[0]["base_value"] == "1.1"
    assert found[0]["unit"] is None and found[0]["base_unit"] is None

    damping = extract_cell_values("≥ 0,06")
    assert [(v["op"], v["base_value"], v["base_unit"]) for v in damping] \
        == [(">=", "0.06", None)]


def test_a_bare_integer_in_a_cell_is_not_a_value():
    """``7`` in a cell is a row number, a footnote mark or an ordinal -- variant B.

    This is the whole point of the gate. Variant A would take it and flood the cell
    inventory with numbers that match any claim; the check of AP-31 would then prove
    nothing.
    """
    assert extract_cell_values("7") == []
    assert extract_cell_values("Funktion 1") == []
    assert extract_cell_values("1 | 2 | 3") == []


def test_paragraph_extraction_is_unchanged():
    """``extract_values`` over paragraph text, character for character as before.

    Part A must not touch ``paragraphs[].values``, and with them the paragraph diff,
    ``kennwert_changes`` and the synopsis. The bare ``0,06`` in the text stays invisible
    on purpose.
    """
    assert json.dumps(extract_values(PARAGRAPH), ensure_ascii=False, sort_keys=True) \
        == json.dumps(PARAGRAPH_VALUES, ensure_ascii=False, sort_keys=True)


def test_the_cell_variant_is_a_separate_function():
    """No switch inside ``extract_values`` that a later caller could flip by accident.

    The cell variant is its own name, it uses ``extract_values`` and puts its extra hits
    beside those -- the paragraph path cannot reach the wider rule at all.
    """
    assert list(inspect.signature(extract_values).parameters) == ["text"]
    assert extract_cell_values is not extract_values
    assert extract_values("0,06") == []
    assert len(extract_cell_values("0,06")) == 1


def test_a_unitless_value_never_matches_a_unit_value():
    """``(None, "0.06")`` and ``("s", "0.06")`` are two different values.

    Without the separation a damping factor of 0,06 would confirm a claim about 0,06
    seconds. The comparison key keeps carrying the unit, and ``None`` is a unit like any
    other in it -- one that only equals itself.
    """
    bare = value_keys([{"row": 0, "col": 0, "values": extract_cell_values("0,06")}])
    with_unit = value_keys([{"row": 0, "col": 0, "values": extract_cell_values("0,06 s")}])
    assert bare == {(None, "0.06")}
    assert with_unit == {("s", "0.06")}
    assert not (bare & with_unit)


def test_a_value_with_a_unit_still_wins():
    """``≤ 500 kW`` stays one value with a unit, it does not become two.

    The bare number is only looked for where no value with a unit already covers it;
    otherwise every limit value would be counted twice and every count of this package
    would double.
    """
    assert extract_cell_values("≤ 500 kW") == extract_values("≤ 500 kW")
    assert len(extract_cell_values("≤ 500 kW")) == 1

    mixed = extract_cell_values("Statik 5 %, Dämpfung ≥ 0,06")
    assert [(v["value"], v["base_unit"]) for v in mixed] == [("5", "%"), ("0.06", None)]

    ranged = extract_cell_values("2,5 bis 12,5 %")
    assert len(ranged) == 1 and ranged[0]["base_unit"] == "%"


# -- part B: the thousands separator ----------------------------------------------------

def test_a_space_thousands_separator_is_read():
    """``1 000 ms`` is 1000 ms, not 0.

    The docstring promised thousands separators and the space was not one of them, so
    the value came out silently **wrong** rather than missing -- the worse of the two
    failures. Measured over the six documents: four values change, all of them the
    ``1 000 V`` of 60909.
    """
    found = extract_values("1 000 ms")
    assert len(found) == 1
    assert found[0]["value"] == "1000" and found[0]["base_unit"] == "s"
    assert found[0]["raw"] == "1 000 ms"

    assert [v["value"] for v in extract_values("100 V bis 1 000 V")] == ["100", "1000"]


def test_a_space_does_not_join_two_numbers():
    """Exactly three digits behind the space, and no further digit behind those.

    ``Anhang 1 000 Fälle`` yields no value at all -- without a unit nothing is a value,
    so the join cannot become visible there. Where it could become visible, the guard
    holds: ``1 0000 V`` is not ten thousand volts.
    """
    assert extract_values("Anhang 1 000 Fälle") == []
    assert extract_cell_values("Anhang 1 000 Fälle") == []

    joined = extract_values("1 0000 V")
    assert all(v["value"] != "10000" for v in joined)
    assert [v["value"] for v in extract_values("Bild 2, 500 kW")] == ["500"]


def test_the_dot_thousands_separator_still_works():
    """``1.000 ms`` keeps reading 1000 ms -- the branch that always worked."""
    found = extract_values("1.000 ms")
    assert len(found) == 1
    assert found[0]["value"] == "1000" and found[0]["base_value"] == "1"
    assert [v["value"] for v in extract_values("1.234,56 kV")] == ["1234.56"]


# -- what the new values do to the statistics and to an older document -------------------

CAPTION = "Tabelle 14 – Dynamische Anforderungen"
CELLS = [["EZE Technologie", "Pub,min", "Dämpfungsmaß"],
         ["Gasturbine ≤ 2 MW", "10 %", "≥ 0,06"],
         ["Dampfturbine", "20 %", "≥ 0,06"]]


def _table(tid: str, enriched: bool = True, pre_ap34: bool = False) -> dict:
    t = {"id": tid, "caption": CAPTION, "cells": CELLS, "n_rows": len(CELLS),
         "n_cols": 3, "para_anchor": "1.p1"}
    if pre_ap34:
        # what the enrichment wrote between AP-31 and AP-33: values with a unit only
        t["cell_values"] = [{"row": r, "col": c, "values": extract_values(cell)}
                            for r, row in enumerate(CELLS)
                            for c, cell in enumerate(row) if extract_values(cell)]
    elif enriched:
        t["cell_values"] = cell_values(CELLS)
    return t


def _doc(tid: str, **over) -> dict:
    return {"doc_id": "X", "title": "T", "version_label": "v",
            "source": {"filename": "a.docx", "format": "docx"},
            "sections": [{"id": "1", "title": "Betrieb", "level": 1, "part": "hauptteil",
                          "paragraphs": [{"id": "1.p1", "n0": "Text.", "n1": "Text."}],
                          "tables": [_table(tid, **over)],
                          "figures": [], "formulas": []}]}


def _synopse() -> dict:
    return {"pair": "test", "chapters": [
        {"old_id": "1", "new_id": "1", "mapping_id": "1<1", "title": "Betrieb",
         "part": "hauptteil", "mode": "changed", "n_identical": 0,
         "old_ids": ["1"], "new_ids": ["1"],
         "tables_diff": [{"kind": "matched", "old": "alt_tab_1", "new": "neu_tab_1",
                          "caption": CAPTION, "identical": True, "rows_changed": 0,
                          "confidence": 1.0}],
         "changes": []}]}


def _entry(**over) -> dict:
    d = {"table": CAPTION, "table_ref": "neu_tab_1", "status": "changed",
         "change": "Das Dämpfungsmaß ist gefordert.",
         "value_changes": ["Gasturbine: Pub,min 10 % PrE, Dämpfung ≥0,06"],
         "impact": "-", "evidence": CAPTION, "confidence": "high"}
    d.update(over)
    return d


def _check(entry: dict, old_doc: dict, new_doc: dict) -> dict:
    ch = _synopse()["chapters"][0]
    om, nm = ({t["id"]: t for s in doc["sections"] for t in s.get("tables") or []}
              for doc in (old_doc, new_doc))
    return check_asset_values(entry, resolve_table(entry, ch), om, nm,
                              ch.get("tables_diff"))


def test_statistics_count_the_new_values(tmp_path):
    """``cell_values_unexamined`` rises by exactly the values the cells gained.

    The number keeps its meaning -- how many cell values no deterministic stage compared
    -- so the jump has to be explainable, not surprising: two damping factors per
    edition on top of the two percentages.
    """
    old_doc, new_doc = _doc("alt_tab_1"), _doc("neu_tab_1")
    res = build_statistics(old_doc, new_doc, _synopse(), tmp_path / "statistics.json")
    assert res["comparison"]["cell_values_unexamined"] == {"old": 4, "new": 4, "total": 8}

    pre = build_statistics(_doc("alt_tab_1", pre_ap34=True),
                           _doc("neu_tab_1", pre_ap34=True),
                           _synopse(), tmp_path / "vorher.json")
    assert pre["comparison"]["cell_values_unexamined"] == {"old": 2, "new": 2, "total": 4}


def test_the_bare_value_of_a_cell_is_checked(tmp_path):
    """End to end: the claim "Dämpfung ≥0,06" is now checked against the cell.

    Before AP-34 the entry had nothing to check -- ``values_checked`` counted the 10 %
    and stopped there, and the damping factor, the actual subject of the table, passed
    unexamined.
    """
    res = _check(_entry(), _doc("alt_tab_1"), _doc("neu_tab_1"))
    assert res["values_checked"] == 2
    assert res["values_found"] == 2
    assert res["values_ok"] is True

    wrong = _check(_entry(value_changes=["Dämpfung ≥0,08"]),
                   _doc("alt_tab_1"), _doc("neu_tab_1"))
    assert (wrong["values_checked"], wrong["values_found"]) == (1, 0)
    assert wrong["values_ok"] is False


def test_a_unitless_claim_does_not_match_a_cell_with_a_unit():
    """The separation holds where it costs: "0,06" is not confirmed by "0,06 s"."""
    doc = _doc("neu_tab_1")
    doc["sections"][0]["tables"][0]["cells"] = [["Zeit"], ["0,06 s"]]
    doc["sections"][0]["tables"][0]["cell_values"] = cell_values([["Zeit"], ["0,06 s"]])
    res = _check(_entry(value_changes=["Dämpfung ≥0,06"]), _doc("alt_tab_1"), doc)
    assert (res["values_checked"], res["values_found"]) == (1, 0)


def test_an_older_norm_doc_stays_readable():
    """A ``norm_doc.json`` from before AP-34 loads and is checked all the same.

    Its ``cell_values`` carries the values with a unit only. Such a run measures
    conservatively -- more checked than found -- but it never reports a hit that is not
    there, and it never raises.
    """
    old_doc, new_doc = _doc("alt_tab_1", pre_ap34=True), _doc("neu_tab_1", pre_ap34=True)
    res = _check(_entry(), old_doc, new_doc)
    assert res["values_checked"] == 2
    assert res["values_found"] == 1                 # 10 % yes, the damping factor no
    assert res["values_ok"] is False

    # and a document without the field at all is computed on the fly, with the new rule
    fresh_old, fresh_new = _doc("alt_tab_1", enriched=False), _doc("neu_tab_1",
                                                                  enriched=False)
    assert "cell_values" not in fresh_new["sections"][0]["tables"][0]
    assert _check(_entry(), fresh_old, fresh_new)["values_found"] == 2
