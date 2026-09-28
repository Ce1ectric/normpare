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

from ..stages.deutung import (
    coverage_percent,
    coverage_total,
    coverage_unanswered,
    format_percent,
)
from .axes import change_rows

#: The columns, in this order. Axis D is one column (see :data:`COMPONENT_SEPARATOR`),
#: the two older labels stay in -- AP-17 shows the axes, it does not replace anything.
#: ``section_old``/``section_new`` (AP-41) are appended at the **end**: ``section_id`` is
#: the head of the chapter mapping and keeps column 1, and an evaluation that reads this
#: file by column position does not shift.
CSV_COLUMNS = (
    "section_id", "mapping_id", "chapter_title", "change_index", "change_kind",
    "structural_operation", "semantic_status", "normative_direction",
    "affected_components", "indeterminate_reason", "semantic_label", "obligation",
    "change", "impact", "evidence", "evidence_ok", "confidence",
    "section_old", "section_new",
)

#: German Excel opens a comma-separated file into a single column.
DELIMITER = ";"

#: With the BOM Excel reads the file as UTF-8 without an import dialog.
ENCODING = "utf-8-sig"

#: Axis D is multi-valued and stays in one cell: the pipe survives a text filter and
#: appears in no value of the vocabulary.
COMPONENT_SEPARATOR = "|"


#: The companion text beside the CSV, named after it. The note cannot go *into* the file:
#: a line in front of the header would make the column names data for every reader, and
#: this export exists to be read by a program and by Excel (AP-19).
COVERAGE_NOTE_SUFFIX = "_Abdeckung.txt"


def coverage_note(coverage: dict | None) -> str:
    """The companion text when the run interpreted only part of its changes, else ``""``.

    First line is the headline: how many of how many, in percent. Everything a reader of
    the spreadsheet needs to know before filtering it into a course outline.

    Written when a change that was shown to a prompt came back without an interpretation
    (AP-28); see :func:`normpare.report.component_view.coverage_note`.
    """
    if not coverage or coverage_unanswered(coverage) <= 0:
        return ""
    missing = coverage_unanswered(coverage)
    lines = [(f"UNVOLLSTÄNDIG: {coverage['n_interpreted']} von {coverage_total(coverage)} "
              f"Änderungen sind gedeutet ({format_percent(coverage_percent(coverage))} %)."),
             "",
             (f"Die übrigen {missing} Änderungen sind deterministisch erfasst, aber "
              "ungedeutet — sie"),
             ("haben in dieser Datei keine Zeile. Betroffene Kapitel "
              "(ungedeutet von gesamt):"), ""]
    lines += [f"  {c['mapping_id'] or c['section_id']}: "
              f"{c['n_changes'] - c['n_interpreted']} von {c['n_changes']}"
              for c in coverage.get("incomplete") or []]
    return "\n".join(lines) + "\n"


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
    """Write ``Aenderungen_<run>.csv`` beside the other deliverables.

    Incomplete coverage adds ``Aenderungen_<run>_Abdeckung.txt``; complete coverage
    removes it, so a repeat run into the same directory cannot leave a warning behind
    that no longer holds.
    """
    out = write_changes_csv(change_rows(synopse, deutung), out_path)
    note = out.with_name(out.stem + COVERAGE_NOTE_SUFFIX)
    text = coverage_note((deutung or {}).get("coverage"))
    if text:
        note.write_text(text, encoding="utf-8")
    elif note.exists():
        note.unlink()
    return out
