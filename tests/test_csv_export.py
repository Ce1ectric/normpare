"""Test the table CSV export."""
from __future__ import annotations

import csv

from normpare.report.csv_export import export_tables_csv


def test_export_writes_one_csv_per_table(tmp_path):
    doc = {"sections": [
        {"id": "5", "tables": [
            {"id": "d_tab_001", "cells": [["Größe", "Wert"], ["U", "20 kV"]]},
            {"id": "d_tab_002", "cells": [["a", "b", "c"]]},
        ]},
        {"id": "6", "tables": []},
    ]}
    written = export_tables_csv(doc, tmp_path)
    assert sorted(written) == ["assets/tables/d_tab_001.csv", "assets/tables/d_tab_002.csv"]

    rows = list(csv.reader((tmp_path / "assets/tables/d_tab_001.csv").open(encoding="utf-8")))
    assert rows == [["Größe", "Wert"], ["U", "20 kV"]]


def test_skips_tables_without_cells(tmp_path):
    doc = {"sections": [{"id": "1", "tables": [{"id": "empty"}]}]}
    assert export_tables_csv(doc, tmp_path) == []
