"""Export section tables as CSV files into the document's ``assets/tables/`` folder.

Each section-level table (its cell matrix) is written to ``assets/tables/<table_id>.csv``,
so the tables are available as plain, tool-friendly data next to the document.
"""
from __future__ import annotations

import csv
from pathlib import Path


def export_tables_csv(doc: dict, out_dir) -> list[str]:
    """Write each section table's cells to ``<out_dir>/assets/tables/<id>.csv``.

    Args:
        doc: a ``norm_doc`` dict (its sections carry the tables).
        out_dir: the document's output directory (e.g. ``out/neu``).

    Returns:
        The CSV paths written, relative to ``out_dir`` (e.g. ``assets/tables/x_tab_001.csv``).
    """
    out_dir = Path(out_dir)
    dest = out_dir / "assets" / "tables"
    written: list[str] = []
    for section in doc.get("sections", []):
        for table in section.get("tables", []) or []:
            cells = table.get("cells")
            table_id = table.get("id")
            if not cells or not table_id:
                continue
            dest.mkdir(parents=True, exist_ok=True)
            rel = Path("assets") / "tables" / f"{table_id}.csv"
            with open(out_dir / rel, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                for row in cells:
                    writer.writerow(["" if c is None else str(c) for c in row])
            written.append(str(rel))
    return written
