"""Tests for normpare.stages.diff.tables_diff (table pairing old<->new)."""
from normpare.stages.diff import tables_diff


def _sec(tables):
    return [{"tables": tables}]


def test_matched_identical():
    t = {"id": "t", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "2"]], "n_rows": 2, "n_cols": 2}
    out = tables_diff(_sec([dict(t, id="o")]), _sec([dict(t, id="n")]), None)
    assert len(out) == 1
    assert out[0]["kind"] == "matched"
    assert out[0]["identical"] is True
    assert out[0]["rows_changed"] == 0


def test_matched_changed():
    o = {"id": "o", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "2"]], "n_rows": 2, "n_cols": 2}
    n = {"id": "n", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "3"]], "n_rows": 2, "n_cols": 2}
    out = tables_diff(_sec([o]), _sec([n]), None)
    assert out[0]["kind"] == "matched"
    assert out[0]["identical"] is False
    assert out[0]["rows_changed"] >= 1


def test_new_and_removed():
    # clearly dissimilar captions -> no pairing
    o = {"id": "o", "caption": "Tabelle A - Grenzwerte der Oberschwingungen",
         "cells": [["x", "1"]], "n_rows": 1, "n_cols": 2}
    n = {"id": "n", "caption": "Tabelle Z - Antragsformular Seite 1",
         "cells": [["y", "2"]], "n_rows": 1, "n_cols": 2}
    out = tables_diff(_sec([o]), _sec([n]), None)
    kinds = sorted(t["kind"] for t in out)
    assert kinds == ["new", "removed"]
