"""Deterministic ordering of the value and formula diffs (reproducibility).

The reference implementation iterated raw ``set`` differences here, so the order of the lists
varied between runs (Python hash randomization). normpare sorts them for stable output.
"""
from __future__ import annotations

from normpare.stages.diff import formulas_diff
from normpare.stages.enrich.values import diff_values


def test_diff_values_removed_sorted_by_unit():
    d = diff_values("10 kV, 5 A, 3 s", "")
    units = [r["base_unit"] for r in d["removed"]]
    assert units == sorted(units) and len(units) >= 2


def test_formulas_diff_added_sorted():
    old = [{"formulas": []}]
    new = [{"formulas": [
        {"id": "n_c", "linear": "c=3"},
        {"id": "n_a", "linear": "a=1"},
        {"id": "n_b", "linear": "b=2"},
    ]}]
    reprs = [x["repr"] for x in formulas_diff(old, new)["added"]]
    assert reprs == sorted(reprs) and len(reprs) == 3
