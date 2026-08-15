"""Every interpreted change as one CSV row -- the fastest way from the axes to an outline.

The JSON artifacts answer questions a program asks. This file answers the questions a
person asks in a spreadsheet: filter axis D to ``proof_obligation``, pivot the direction
against the chapter, sort by confidence. One row per interpretation, no bundling, no
selection.

Two deliberate deviations from :mod:`normpare.report.csv_export`, which exports document
tables for tools: the file is written as **UTF-8 with BOM** and separated by
**semicolons**, because it is opened by a double click in a German Excel and neither
umlauts nor columns may fall apart there. ``csv_export`` keeps its plain UTF-8 comma
form; the two exports have different readers.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .axes import change_rows

#: The columns, in this order. Axis D is one column (see :data:`COMPONENT_SEPARATOR`),
#: the two older labels stay in -- AP-17 shows the axes, it does not replace anything.
CSV_COLUMNS = (
    "section_id", "mapping_id", "chapter_title", "change_index", "change_kind",
    "structural_operation", "semantic_status", "normative_direction",
    "affected_components", "indeterminate_reason", "semantic_label", "obligation",
    "change", "impact", "evidence", "evidence_ok", "confidence",
)

#: German Excel opens a comma-separated file into a single column.
DELIMITER = ";"

#: With the BOM Excel reads the file as UTF-8 without an import dialog.
ENCODING = "utf-8-sig"

#: Axis D is multi-valued and stays in one cell: the pipe survives a text filter and
#: appears in no value of the vocabulary.
COMPONENT_SEPARATOR = "|"


def _cell(row: dict, column: str) -> str:
    value = row.get(column, "")
    if column == "affected_components":
        return COMPONENT_SEPARATOR.join(value or [])
    return value


def write_changes_csv(rows: list[dict], out_path: str | Path) -> Path:
    """Write the given rows; the header is always written, even for an empty run."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding=ENCODING) as fh:
        writer = csv.writer(fh, delimiter=DELIMITER)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([_cell(row, column) for column in CSV_COLUMNS])
    return out


def build_changes_csv(synopse: dict | None, deutung: dict | None,
                      out_path: str | Path) -> Path:
    """Write ``Aenderungen_<run>.csv`` beside the other deliverables."""
    return write_changes_csv(change_rows(synopse, deutung), out_path)
