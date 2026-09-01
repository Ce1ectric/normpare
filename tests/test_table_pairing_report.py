"""AP-35: why tables without a caption do not pair (``tools/table_pairing_report.py``).

Most tables without a counterpart carry no usable caption -- 38 of 40 removed ones at
4110, 21 of 23 at 4120. In an unpaired table every changed value is invisible, and that
is the largest remaining blind spot of the comparison.

The caption is not the cause: :func:`table_similarity` has measured *both* signals since
AP-22 and takes the stronger one, so a captionless table is paired over its content long
since. The open question is why that does not catch these tables. Three explanations are
possible and they lead to different packages -- the chapter boundary (the candidate is
only reachable through ``cross_chapter_tables`` and its higher threshold), displacement
(the Hungarian assignment gives every new table away once) or no partner at all.

This package measures, it changes nothing: no threshold, no pairing rule, not a line of
production code. Every test builds its own miniature run; none reads ``out/``, none needs
real standard text and none touches the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from normpare.stages import diff
from normpare.stages.diff import (
    CONTENT_CHARS,
    CONTENT_ROWS,
    CROSS_CHAPTER_MIN,
    TABLE_MATCH_MIN,
    build_synopse,
    table_similarity,
)

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "table_pairing_report", ROOT / "tools" / "table_pairing_report.py")
table_pairing_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(table_pairing_report)


# --- miniature documents ----------------------------------------------------------------

HEAD = ["Nr.", "Bezeichnung", "Wert"]

#: Two identical tables -- the pair every scenario needs so that "paired" is not empty.
LIMITS = [HEAD, ["1", "Kurzschlussleistung", "500 MVA"], ["2", "Bemessungsspannung", "20 kV"]]
#: The same subject, rewritten: similar enough to be a candidate, not the better partner.
LIMITS_REWRITTEN = [HEAD, ["1", "Kurzschlussleistung", "800 MVA"], ["7", "Zeitkonstante", "3 s"]]
#: A different subject altogether -- no candidate for anything above.
SCHEDULE = [["Anlage", "Pruefung"], ["Wandler", "jaehrlich"], ["Schalter", "alle zwei Jahre"]]
#: And a third one, for the runs that need two unrelated tables.
TERMS = [["Begriff", "Definition"], ["Netzbetreiber", "betreibt das Netz"],
         ["Anschlussnehmer", "schliesst an"]]


def _table(tid: str, cells: list[list[str]], caption: str | None = None) -> dict:
    return {"id": tid, "caption": caption, "n_rows": len(cells), "n_cols": len(cells[0]),
            "cells": cells, "para_anchor": None}


def _para(pid: str, text: str) -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": "text", "modality": "informativ",
            "modality_counts": {}, "refs_internal": [], "refs_external": [], "values": []}


def _section(sid: str, tables: list[dict]) -> dict:
    return {"id": sid, "title": f"Kapitel {sid}", "level": 1, "part": "main",
            "paragraphs": [_para(f"{sid}.p1", f"Der Abschnitt {sid} enthaelt Tabellen.")],
            "tables": tables, "figures": [], "formulas": []}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "sections": sections,
            "source": {"format": "docx", "sha256": "0" * 8}}


def _record(old_id: str, new_id: str) -> dict:
    return {"old_id": old_id, "new_id": new_id, "old_ids": [old_id], "new_ids": [new_id],
            "old_title": f"Kapitel {old_id}", "new_title": f"Kapitel {new_id}",
            "old_level": 1, "new_level": 1, "old_part": "main", "new_part": "main",
            "match_type": "id+title", "confidence": 1.0, "part_changed": False,
            "para_links": [{"old_ids": [f"{old_id}.p1"], "new_ids": [f"{new_id}.p1"],
                            "kind": "identical", "confidence": 1.0}]}


def _run_dir(tmp_path: Path, old_doc: dict, new_doc: dict, records: list[dict],
             name: str = "run") -> Path:
    """A finished run as the tool reads it: both ``norm_doc.json`` and the synopsis."""
    dest = tmp_path / name
    (dest / "alt").mkdir(parents=True)
    (dest / "neu").mkdir(parents=True)
    (dest / "alt" / "norm_doc.json").write_text(
        json.dumps(old_doc, ensure_ascii=False), encoding="utf-8")
    (dest / "neu" / "norm_doc.json").write_text(
        json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
    build_synopse(old_doc, new_doc, records, None, dest / "synopse.json", "alt <-> neu")
    return dest


def _paired_and_leftovers(tmp_path: Path) -> Path:
    """One chapter: a table that pairs, one old and one new that do not."""
    old = _doc("old", [_section("1", [_table("o_pair", LIMITS), _table("o_alone", SCHEDULE)])])
    new = _doc("new", [_section("1", [_table("n_pair", LIMITS), _table("n_alone", TERMS)])])
    return _run_dir(tmp_path, old, new, [_record("1", "1")])


def _row(run: dict, table_id: str) -> dict:
    return next(r for r in run["rows"] if r["table"] == table_id)


# --- 1. the base set --------------------------------------------------------------------

def test_the_report_counts_the_unpaired_tables(tmp_path):
    """Per side and kind: how many tables there are, how many pair, how many do not."""
    assert table_similarity(_table("a", LIMITS), _table("b", LIMITS)) == 1.0
    assert table_similarity(_table("a", SCHEDULE), _table("b", TERMS)) < TABLE_MATCH_MIN

    run = table_pairing_report.load(_paired_and_leftovers(tmp_path))
    inv = run["inventory"]
    assert inv["old"]["tables"] == 2
    assert inv["new"]["tables"] == 2
    assert inv["old"]["matched"] == 1
    assert inv["new"]["matched"] == 1
    assert inv["old"]["unpaired"] == 1
    assert inv["new"]["unpaired"] == 1
    assert {r["table"] for r in run["rows"]} == {"o_alone", "n_alone"}
    # the reconstruction of the record must agree with what the run recorded
    assert run["inventory"]["reconstruction_mismatches"] == 0


def test_a_fragment_table_is_excluded(tmp_path):
    """``_is_fragment_table`` drops out of the base set -- it is not a table."""
    fragment = _table("o_frag", [["(11)"]])
    assert diff._is_fragment_table(fragment)
    old = _doc("old", [_section("1", [_table("o_pair", LIMITS), fragment])])
    new = _doc("new", [_section("1", [_table("n_pair", LIMITS)])])

    run = table_pairing_report.load(_run_dir(tmp_path, old, new, [_record("1", "1")]))
    inv = run["inventory"]
    assert inv["old"]["tables"] == 2
    assert inv["old"]["fragments"] == 1
    assert inv["old"]["in_records"] == 1
    assert inv["old"]["unpaired"] == 0
    assert "o_frag" not in {r["table"] for r in run["rows"]}


def test_a_captionless_table_is_recognised_as_such(tmp_path):
    """An empty ``caption_text`` counts as "without a caption" -- and so does a broken one."""
    old = _doc("old", [_section("1", [
        _table("o_none", SCHEDULE),
        _table("o_broken", SCHEDULE, caption="Tabelle 10 empfohlen."),
        _table("o_good", SCHEDULE, caption="Tabelle 5 – Pruefungen der Anlage")])])
    new = _doc("new", [_section("1", [_table("n_other", TERMS)])])

    run = table_pairing_report.load(_run_dir(tmp_path, old, new, [_record("1", "1")]))
    assert run["inventory"]["old"]["unpaired"] == 3
    assert run["inventory"]["old"]["unpaired_captionless"] == 2
    assert _row(run, "o_none")["captioned"] is False
    assert _row(run, "o_broken")["captioned"] is False
    assert _row(run, "o_good")["captioned"] is True
    assert _row(run, "o_good")["caption"] == "Pruefungen der Anlage"


# --- 2. the distribution ----------------------------------------------------------------

def test_the_best_candidate_uses_the_production_similarity(tmp_path):
    """The reported score is ``table_similarity``, not a rebuild of it."""
    old = _doc("old", [_section("1", [_table("o_alone", LIMITS_REWRITTEN)])])
    new = _doc("new", [_section("2", [_table("n_alone", LIMITS)])])
    records = [_record("1", "1"), _record("2", "2")]
    old["sections"].append(_section("2", []))
    new["sections"].insert(0, _section("1", []))

    run = table_pairing_report.load(_run_dir(tmp_path, old, new, records))
    expected = table_similarity(_table("o", LIMITS_REWRITTEN), _table("n", LIMITS))
    assert _row(run, "o_alone")["best_unpaired"]["score"] == expected
    assert _row(run, "n_alone")["best_unpaired"]["score"] == expected


def test_the_histogram_bins_are_stable(tmp_path):
    """Fixed steps of 0.05, twenty of them, empty classes included."""
    labels = table_pairing_report.BIN_LABELS
    assert len(labels) == 20
    assert labels[0] == "0.00-0.05"
    assert labels[11] == "0.55-0.60"
    assert labels[-1] == "0.95-1.00"

    assert table_pairing_report.histogram([]) == [0] * 20
    counted = table_pairing_report.histogram([0.0, 0.549, 0.55, 0.999, 1.0])
    assert len(counted) == 20
    assert counted[0] == 1
    assert counted[10] == 1        # 0.50-0.55
    assert counted[11] == 1        # 0.55-0.60
    assert counted[19] == 2        # 0.95-1.00, the closed upper end

    run = table_pairing_report.load(_paired_and_leftovers(tmp_path))
    for key in ("all", "captioned", "captionless"):
        assert len(run["histogram"][key]) == 20
    assert sum(run["histogram"]["all"]) == len(
        [r for r in run["rows"] if r["best_unpaired"]])


# --- 3. the three explanations ----------------------------------------------------------

def test_a_candidate_in_the_same_record_is_called_displacement(tmp_path):
    """Explanation 2: the partner went to a competitor inside the same record."""
    old = _doc("old", [_section("1", [_table("o_best", LIMITS),
                                      _table("o_lost", LIMITS_REWRITTEN)])])
    new = _doc("new", [_section("1", [_table("n_one", LIMITS)])])
    assert table_similarity(_table("a", LIMITS_REWRITTEN), _table("b", LIMITS)) >= TABLE_MATCH_MIN

    run = table_pairing_report.load(_run_dir(tmp_path, old, new, [_record("1", "1")]))
    row = _row(run, "o_lost")
    assert row["explanation"] == "displacement"
    assert row["candidate"]["table"] == "n_one"
    assert row["competitor"] == "o_best"
    assert run["explanations"]["displacement"] == 1


def test_a_candidate_in_another_record_is_called_chapter_boundary(tmp_path):
    """Explanation 1: only ``cross_chapter_tables`` could reach it, and 0.80 is too high."""
    old = _doc("old", [_section("1", [_table("o_alone", LIMITS_REWRITTEN)]), _section("2", [])])
    new = _doc("new", [_section("1", []), _section("2", [_table("n_alone", LIMITS)])])
    records = [_record("1", "1"), _record("2", "2")]
    score = table_similarity(_table("a", LIMITS_REWRITTEN), _table("b", LIMITS))
    assert TABLE_MATCH_MIN <= score < CROSS_CHAPTER_MIN

    run = table_pairing_report.load(_run_dir(tmp_path, old, new, records))
    row = _row(run, "o_alone")
    assert row["explanation"] == "chapter_boundary"
    assert row["candidate"]["table"] == "n_alone"
    assert row["candidate"]["score"] == score
    assert row["below_cross_chapter_min"] is True
    assert run["explanations"]["chapter_boundary"] == 2      # both ends of the same pair
    assert run["cross_chapter"]["below_min"] == 2


def test_a_table_without_any_candidate_is_counted_separately(tmp_path):
    """Explanation 3: nothing on the other side reaches ``TABLE_MATCH_MIN``."""
    run = table_pairing_report.load(_paired_and_leftovers(tmp_path))
    row = _row(run, "o_alone")
    assert row["explanation"] == "no_candidate"
    assert row["candidate"] is None
    assert run["explanations"]["no_candidate"] == 2
    assert run["explanations"]["displacement"] == 0
    assert run["explanations"]["chapter_boundary"] == 0


# --- the tool itself --------------------------------------------------------------------

def test_the_tool_writes_only_to_out(tmp_path):
    """``guard_write`` refuses every destination below ``out/``."""
    run = _paired_and_leftovers(tmp_path)
    forbidden = ROOT / "out" / "table_pairing"
    with pytest.raises(table_pairing_report.regression.WriteToOutError):
        table_pairing_report.main(["--dir", str(run), "--out", str(forbidden)])
    assert not forbidden.exists()

    dest = tmp_path / "report"
    assert table_pairing_report.main(["--dir", str(run), "--out", str(dest)]) == 0
    written = sorted(p.name for p in dest.iterdir())
    assert written == ["table_pairing_run.txt"]


def test_the_tool_takes_several_runs(tmp_path):
    """Several ``--dir``, one comparison column each, in the order of the command line."""
    first = _paired_and_leftovers(tmp_path / "a")
    second = _paired_and_leftovers(tmp_path / "b")
    dest = tmp_path / "report"
    assert table_pairing_report.main(
        ["--dir", str(first), "--dir", str(second), "--out", str(dest)]) == 0
    names = sorted(p.name for p in dest.iterdir())
    assert "table_pairing_comparison.txt" in names

    runs = [table_pairing_report.load(first, label="first"),
            table_pairing_report.load(second, label="second")]
    text = table_pairing_report.render_comparison(runs)
    assert text.index("first") < text.index("second")
    assert table_pairing_report.render_comparison(runs) == text      # deterministic


def test_no_production_constant_is_touched(tmp_path):
    """The thresholds are imported unchanged and nothing writes them back."""
    frozen = (diff.TABLE_MATCH_MIN, diff.CROSS_CHAPTER_MIN,
              diff.CONTENT_ROWS, diff.CONTENT_CHARS)
    assert (table_pairing_report.TABLE_MATCH_MIN,
            table_pairing_report.CROSS_CHAPTER_MIN) == (TABLE_MATCH_MIN, CROSS_CHAPTER_MIN)

    run = _paired_and_leftovers(tmp_path)
    table_pairing_report.main(["--dir", str(run), "--out", str(tmp_path / "report")])

    assert (diff.TABLE_MATCH_MIN, diff.CROSS_CHAPTER_MIN,
            diff.CONTENT_ROWS, diff.CONTENT_CHARS) == frozen
    assert (TABLE_MATCH_MIN, CROSS_CHAPTER_MIN, CONTENT_ROWS, CONTENT_CHARS) == frozen
