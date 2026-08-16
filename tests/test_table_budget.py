"""Tests for the table and asset-block budgets of the interpretation prompt (AP-20).

``_render_cells`` capped the cell text of **each** table at 1100 characters and
``chapter_assets_block`` the whole table/figure block at 6500 -- both without a word in
the prompt. Measured over the two production runs that hit 115 of 233 tables (4110) and
89 of 171 (4120): half of the tables, and tables are where the limit values live.

The budgets are raised to values that cover every table of both corpora (largest
measured: 6752 and 6645 characters) and every block (26 445 and 29 694). What these tests
pin down is not the number but the silence: a cap that still bites has to say so in the
prompt **and** be counted in the coverage report -- including the zero, so that "0 of
233" is a measured zero rather than an unobserved one.

No test here touches the network; the interpretation is driven by a fake provider.
"""
from __future__ import annotations

import inspect
import io
import json
import token
import tokenize
from pathlib import Path

from normpare.stages.deutung import (
    ASSET_BLOCK_CHARS,
    TABLE_CHARS,
    _render_cells,
    chapter_assets_block,
    coverage_report,
    run_deutung,
    truncation_report,
)

ROW_CHARS = 500            # one cell per row, so a row is exactly this long
ROW_SEP = " ‖ "            # what _render_cells joins the rows with


# -- material ------------------------------------------------------------------------------

def _table(tid: str, n_rows: int, row_chars: int = ROW_CHARS) -> dict:
    """A table whose rendered cell text is ``n_rows * row_chars`` plus the separators."""
    return {"id": tid, "caption": f"Tabelle {tid}",
            "cells": [[f"{i:03d}".ljust(row_chars, "x")] for i in range(n_rows)],
            "n_rows": n_rows, "n_cols": 1}


def _rendered_len(n_rows: int, row_chars: int = ROW_CHARS) -> int:
    return n_rows * row_chars + (n_rows - 1) * len(ROW_SEP)


def _chapter(tables: list[tuple[str, str]]) -> dict:
    """A chapter whose ``tables_diff`` pairs the given (old id, new id) tables."""
    return {"old_id": "10.2", "new_id": "10.2", "mapping_id": "10.2+10.2",
            "title": "Grenzwerte", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": ["10.2"], "new_ids": ["10.2"],
            "tables_diff": [{"kind": "matched", "old": o, "new": n, "identical": False,
                             "rows_changed": 1, "caption": f"Tabelle {n}"}
                            for o, n in tables],
            "changes": [{"kind": "changed", "old_text": "Die Spannung beträgt 10 kV.",
                         "new_text": "Die Spannung beträgt 20 kV."}]}


def _secs(tables: list[dict]) -> dict:
    return {"10.2": {"id": "10.2", "title": "Grenzwerte", "tables": tables, "figures": [],
                     "paragraphs": [{"id": "10.2.p1", "n0": "Die Spannung beträgt 10 kV.",
                                     "n1": "Die Spannung beträgt 20 kV."}]}}


def _answer() -> dict:
    return {"section_id": "10.2", "summary_old": "Alt.", "summary_new": "Neu.",
            "change_overview": "Grenzwert angehoben.", "training_relevance": "high",
            "keywords": [], "practical_note": "",
            "interpretations": [{"change_index": 0, "change": "Grenzwert",
                                 "evidence": "Die Spannung beträgt 20 kV.",
                                 "impact": "", "confidence": 0.9}]}


class _Provider:
    """Answers every prompt with the same interpretation; records the prompts."""

    live = True

    def __init__(self):
        self.prompts: list[str] = []
        self.outcomes: dict[str, str] = {}

    def resolve(self, items):
        out = {}
        for tag, prompt in items:
            self.prompts.append(prompt)
            self.outcomes[tag] = "ok"
            out[tag] = _answer()
        return out


def _run(tmp_path, chapter: dict, tables: list[dict]) -> tuple[dict, _Provider]:
    provider = _Provider()
    secs = _secs(tables)
    doc = {"sections": list(secs.values())}
    out = run_deutung({"pair": "test", "chapters": [chapter]}, doc, doc, tmp_path,
                      tmp_path / "out", model="test-model", deutung_provider=provider)
    return out, provider


# -- 1..3: the table budget ------------------------------------------------------------------

def test_a_small_table_is_unchanged():
    """A table well below the old cap renders exactly as it did before: rows joined,
    no note of any kind."""
    t = _table("T1", 2, row_chars=100)
    rendered = _render_cells(t)

    assert rendered == ROW_SEP.join(f"{i:03d}".ljust(100, "x") for i in range(2))
    assert "gekürzt" not in rendered
    assert "…" not in rendered


def test_a_table_below_the_new_budget_is_complete():
    """5000 characters used to lose 3900 of them silently; now they arrive."""
    t = _table("T2", 10)                      # 10 * 500 + 9 * 3 = 5027 characters
    rendered = _render_cells(t)

    assert len(rendered) == _rendered_len(10)
    assert rendered.endswith("x")
    assert "gekürzt" not in rendered


def test_an_oversized_table_says_so():
    """Above the budget the text ends with the note, and the note counts the loss."""
    t = _table("T3", 20)                      # 10 057 characters, 2057 over budget
    rendered = _render_cells(t)
    dropped = _rendered_len(20) - TABLE_CHARS

    assert rendered.endswith(f" … (gekürzt, +{dropped} Zeichen)")
    assert rendered.startswith("000xxx")
    assert len(rendered) == TABLE_CHARS + len(f" … (gekürzt, +{dropped} Zeichen)")


# -- 4: the block budget ---------------------------------------------------------------------

def test_an_oversized_block_says_so():
    """A block over the budget is cut, and the cut is named with its size."""
    pairs = [(f"O{i}", f"N{i}") for i in range(6)]        # 12 tables * ~5000 characters
    tables = [_table(tid, 10) for pair in pairs for tid in pair]
    block = chapter_assets_block(_chapter(pairs), _secs(tables), _secs(tables))

    assert block.startswith("\nTABELLEN & BILDER")
    assert "… (Block gekürzt, +" in block
    assert block.rstrip().endswith(" Zeichen)")
    dropped = int(block.rsplit("+", 1)[1].split(" ", 1)[0])
    assert dropped > 0
    assert len(block) == ASSET_BLOCK_CHARS + len(f"\n  … (Block gekürzt, +{dropped} Zeichen)")


# -- 5: the row cap stays as it is -----------------------------------------------------------

def test_the_row_cap_is_untouched():
    """``max_rows`` keeps its value and its own note -- it is rare and already visible."""
    assert inspect.signature(_render_cells).parameters["max_rows"].default == 40
    t = _table("T4", 45, row_chars=10)
    rendered = _render_cells(t)

    assert " … (+5 Zeilen)" in rendered
    assert "gekürzt" not in rendered              # the row cap is not a character cap


# -- 6..8: counting and reporting ------------------------------------------------------------

def test_truncations_are_counted():
    """Every clipped table is recorded with its id, and the counts say how many of how
    many tables that is."""
    pairs = [("O1", "N1"), ("O2", "N2")]
    tables = [_table("O1", 20), _table("N1", 20),        # both over budget
              _table("O2", 2), _table("N2", 2)]          # both well below it
    notes: list[dict] = []
    chapter_assets_block(_chapter(pairs), _secs(tables), _secs(tables), notes=notes)
    report = truncation_report(notes)

    assert report["n_tables"] == 4
    assert report["n_tables_truncated"] == 2
    assert report["n_asset_blocks"] == 1
    assert report["n_asset_blocks_truncated"] == 0
    assert [t["table_id"] for t in report["truncated"]] == ["O1", "N1"]
    assert all(t["n_dropped"] == _rendered_len(20) - TABLE_CHARS
               for t in report["truncated"])


def test_counts_reach_the_coverage_report(tmp_path, capsys):
    """The numbers show up in the console summary, in ``deutung.json`` and in
    ``pipeline_feedback`` -- with the chapter, so a cut can be looked up."""
    pairs = [("O1", "N1")]
    tables = [_table("O1", 20), _table("N1", 2)]
    out, _ = _run(tmp_path, _chapter(pairs), tables)
    cov = out["coverage"]

    assert cov["n_tables"] == 2 and cov["n_tables_truncated"] == 1
    assert cov["n_asset_blocks"] == 1 and cov["n_asset_blocks_truncated"] == 0
    assert cov["truncated"][0]["table_id"] == "O1"
    assert cov["truncated"][0]["mapping_id"] == "10.2+10.2"

    printed = capsys.readouterr().out
    assert "    Tabellen gekürzt: 1 von 2" in printed
    assert "    Asset-Blöcke gekürzt: 0 von 1" in printed

    written = json.loads((tmp_path / "out" / "deutung.json").read_text(encoding="utf-8"))
    assert written["coverage"] == cov
    entry = [fb for fb in out["pipeline_feedback"]
             if fb.get("phase") == "deutung" and fb.get("field") == "coverage"]
    assert len(entry) == 1
    assert entry[0]["n_tables_truncated"] == 1
    assert "1 of 2 table(s) truncated" in entry[0]["finding"]


def test_zero_truncations_is_reported_too(tmp_path, capsys):
    """A run that clips nothing says so. A number that only appears when it is bad
    leaves the good case unmeasured, which is how the 1100 stayed invisible."""
    out, _ = _run(tmp_path, _chapter([("O1", "N1")]), [_table("O1", 2), _table("N1", 2)])

    assert out["coverage"]["n_tables_truncated"] == 0
    assert out["coverage"]["truncated"] == []
    printed = capsys.readouterr().out
    assert "    Tabellen gekürzt: 0 von 2" in printed
    assert "    Asset-Blöcke gekürzt: 0 von 1" in printed
    # ... and a run without any table still reports the pair of lines
    empty = coverage_report([], 0, 0, 0, 0)
    assert empty["n_tables"] == 0 and empty["n_asset_blocks"] == 0


# -- 9: the budgets are constants ------------------------------------------------------------

def test_budgets_are_named_constants():
    """Both budgets are constants with their measurement next to them, not numbers in a
    signature -- the reason 8000 is enough is the measured maximum of 6752."""
    assert TABLE_CHARS == 8000
    assert ASSET_BLOCK_CHARS == 32000
    assert inspect.signature(_render_cells).parameters["max_chars"].default is TABLE_CHARS
    assert (inspect.signature(chapter_assets_block).parameters["max_chars"].default
            is ASSET_BLOCK_CHARS)
    source = Path(inspect.getfile(_render_cells)).read_text(encoding="utf-8")
    numbers = [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
               if t.type == token.NUMBER]
    assert "1100" not in numbers and "6500" not in numbers
    assert numbers.count("8000") == 1          # only where the constant is defined
    assert numbers.count("32000") == 1
