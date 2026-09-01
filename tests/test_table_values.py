"""Limit values in table cells (AP-31).

Two gaps meet here. The deterministic value detection ran over paragraph text only, so a
limit value in a table cell was invisible to it in every run and every standard --
``comparison.kennwert_changes`` reporting 0 for 60909 was a statement about the detector,
not about the standard. And the model reports fifteen times as many value changes as the
deterministic stage knows (138 / 97 / 6 against 9 / 8 / 0), none of them checked against a
single cell.

The package makes them checkable, it does not guess: cells carry their values in
``norm_doc.json`` (part A), a table interpretation names its table by **id** instead of by
its caption (part B), every entry in ``value_changes`` is checked against the cells of that
table and the result is written on the entry (part C), and the statistics say how many cell
values were never examined (part D). Marked, never corrected -- an entry whose values are
not found keeps its text and gains a reason in the review queue.

Synthetic material throughout, no standard, no network.
"""
from __future__ import annotations

import json

from normpare.model.document import Table
from normpare.report.stats import build_statistics
from normpare.stages.deutung import (
    ASSET_SCHEMA_DOC,
    PIPELINE_OWNED_ASSET,
    FixtureProvider,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    chapter_assets_block,
    check_asset_values,
    drop_pipeline_owned,
    resolve_table,
    run_deutung,
    table_key,
)
from normpare.stages.enrich import enrich_doc
from normpare.stages.enrich.values import cell_values, extract_values

MODEL = "test-model"

OLD_TABLE_ID = "alt_tab_003"
NEW_TABLE_ID = "neu_tab_003"

#: The cells both sides are built from: one changed limit value, one unchanged.
OLD_CELLS = [["Parameter", "Wert"], ["Wirkleistung", "≤ 500 kW"], ["Frequenz", "50 Hz"]]
NEW_CELLS = [["Parameter", "Wert"], ["Wirkleistung", "≤ 400 kW"], ["Frequenz", "50 Hz"]]

CAPTION = "Tabelle 3 – Grenzwerte am Netzanschlusspunkt"

OLD_TEXT = "Die Anlage hält die Werte der Tabelle 3 ein."
NEW_TEXT = "Die Anlage hält die Werte der Tabelle 3 jederzeit ein."


def _table(tid: str, cells: list[list[str]], enriched: bool = True) -> dict:
    t = {"id": tid, "caption": CAPTION, "cells": cells,
         "n_rows": len(cells), "n_cols": len(cells[0]), "para_anchor": "1.p1"}
    if enriched:
        t["cell_values"] = cell_values(cells)
    return t


def _doc(tid: str, cells: list[list[str]], text: str, enriched: bool = True) -> dict:
    return {"doc_id": "X", "title": "T", "version_label": "v",
            "source": {"filename": "a.docx", "format": "docx"},
            "sections": [{"id": "1", "title": "Betrieb", "level": 1, "part": "hauptteil",
                          "paragraphs": [{"id": "1.p1", "n0": text, "n1": text}],
                          "tables": [_table(tid, cells, enriched)],
                          "figures": [], "formulas": []}]}


def _docs(enriched: bool = True) -> tuple[dict, dict]:
    return (_doc(OLD_TABLE_ID, OLD_CELLS, OLD_TEXT, enriched),
            _doc(NEW_TABLE_ID, NEW_CELLS, NEW_TEXT, enriched))


def _tables_diff() -> list[dict]:
    return [{"kind": "matched", "old": OLD_TABLE_ID, "new": NEW_TABLE_ID,
             "caption": CAPTION, "identical": False, "rows_changed": 1,
             "confidence": 1.0}]


def _chapter() -> dict:
    return {"old_id": "1", "new_id": "1", "mapping_id": "1<1", "title": "Betrieb",
            "part": "hauptteil", "mode": "changed", "n_identical": 0,
            "old_ids": ["1"], "new_ids": ["1"], "tables_diff": _tables_diff(),
            "changes": [{"kind": "changed", "old_text": OLD_TEXT, "new_text": NEW_TEXT}]}


def _synopse() -> dict:
    return {"pair": "test", "chapters": [_chapter()]}


def _table_entry(**over) -> dict:
    d = {"table": CAPTION, "table_ref": NEW_TABLE_ID, "status": "changed",
         "change": "Der Grenzwert der Wirkleistung sinkt.",
         "value_changes": ["Wirkleistung: 500 kW -> 400 kW"],
         "impact": "Kleinere Anlagen betroffen.",
         "evidence": CAPTION, "confidence": "high"}
    d.update(over)
    return d


def _answer(**over) -> dict:
    d = {"section_id": "1", "summary_old": "Alte Fassung.", "summary_new": "Neue Fassung.",
         "change_overview": "Ein Grenzwert sinkt.", "training_relevance": "high",
         "keywords": [], "practical_note": "",
         "interpretations": [
             {"change_index": 0, "semantic_label": "clarified", "obligation": "unchanged",
              "semantic_status": "clarified", "normative_direction": "unchanged",
              "affected_components": ["limit_value"],
              "change": "Der Satz wird präzisiert.", "impact": "",
              "cross_reference_note": "", "evidence": NEW_TEXT, "confidence": "high",
              "contradiction_flag": False}],
         "tables": [_table_entry()], "figures": []}
    d.update(over)
    return d


def _run(tmp_path, answer: dict, old_doc=None, new_doc=None) -> dict:
    """Interpret the one chapter with a frozen answer -- no provider, no network."""
    old_doc = old_doc if old_doc is not None else _docs()[0]
    new_doc = new_doc if new_doc is not None else _docs()[1]
    syn = _synopse()
    ch = syn["chapters"][0]
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    system = build_system_prompt("de", "")
    user, _sel = build_chapter_prompt(ch, OLD_TEXT, NEW_TEXT, o_secs, n_secs)
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({cache_key(MODEL, system, user): answer},
                               ensure_ascii=False), encoding="utf-8")
    provider = FixtureProvider(path, model=MODEL, system=system)
    return run_deutung(syn, old_doc, new_doc, tmp_path, tmp_path / "out", model=MODEL,
                       deutung_provider=provider)


def _stored_table(out: dict) -> dict:
    return out["chapters"][0]["tables"][0]


# -- part A: the cells carry their values ------------------------------------------------

def test_table_cells_carry_values(tmp_path):
    """A cell with a limit value carries it, normalized exactly as a paragraph value is.

    The same function on the same normal form: "≤ 500 kW" in a cell has to come out as
    the identical record it would produce in running text, otherwise the two halves of
    the value inventory cannot be compared with each other.
    """
    doc = _doc(NEW_TABLE_ID, [["Wirkleistung", "≤ 500 kW"]], NEW_TEXT, enriched=False)
    path = tmp_path / "norm_doc.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    enriched = enrich_doc(path)
    tab = enriched["sections"][0]["tables"][0]
    assert tab["cell_values"] == [{"row": 0, "col": 1,
                                   "values": extract_values("≤ 500 kW")}]
    rec = tab["cell_values"][0]["values"][0]
    # the normal form of the paragraph path, down to Decimal.normalize()'s exponent
    assert (rec["op"], rec["base_value"], rec["base_unit"]) == ("<=", "5E+5", "W")


def test_a_cell_without_a_value_carries_none(tmp_path):
    """Empty and purely textual cells produce no record at all.

    A record per cell would bloat every table by its whole matrix; the field lists the
    cells that carry something, and its length is the count that matters.
    """
    doc = _doc(NEW_TABLE_ID, [["Parameter", ""], ["Wirkleistung", "≤ 400 kW"],
                              ["Bemerkung", "siehe Anhang"]], NEW_TEXT, enriched=False)
    path = tmp_path / "norm_doc.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    tab = enrich_doc(path)["sections"][0]["tables"][0]
    assert [(r["row"], r["col"]) for r in tab["cell_values"]] == [(1, 1)]


def test_paragraph_values_are_unchanged(tmp_path):
    """Part A is additive: the paragraphs come out byte-equal, tables or no tables.

    The enrichment of the cells must not touch a single paragraph field -- every baseline
    of the project rests on them.
    """
    with_tables = _doc(NEW_TABLE_ID, NEW_CELLS,
                       "Der Grenzwert liegt bei 500 kW.", enriched=False)
    without = json.loads(json.dumps(with_tables))
    without["sections"][0]["tables"] = []

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(with_tables, ensure_ascii=False), encoding="utf-8")
    b.write_text(json.dumps(without, ensure_ascii=False), encoding="utf-8")

    sec_a = enrich_doc(a)["sections"][0]
    sec_b = enrich_doc(b)["sections"][0]
    assert (json.dumps(sec_a["paragraphs"], ensure_ascii=False, sort_keys=True)
            == json.dumps(sec_b["paragraphs"], ensure_ascii=False, sort_keys=True))
    assert sec_a["modality_counts"] == sec_b["modality_counts"]


def test_an_older_norm_doc_stays_readable(tmp_path):
    """A ``norm_doc.json`` from before AP-31 loads and is checked all the same.

    The field is additive in both directions: the model round-trip keeps it, a document
    without it round-trips unchanged, and the value check falls back to computing the
    cell values on the fly instead of reporting "unchecked".
    """
    old_style = {"id": NEW_TABLE_ID, "caption": CAPTION, "cells": NEW_CELLS,
                 "n_rows": 3, "n_cols": 2, "para_anchor": "1.p1"}
    assert Table.from_dict(dict(old_style)).to_dict() == old_style
    with_values = _table(NEW_TABLE_ID, NEW_CELLS)
    assert Table.from_dict(dict(with_values)).to_dict() == with_values

    ch = _chapter()
    old_doc, new_doc = _docs(enriched=False)
    om = {t["id"]: t for t in old_doc["sections"][0]["tables"]}
    nm = {t["id"]: t for t in new_doc["sections"][0]["tables"]}
    res = check_asset_values(_table_entry(), resolve_table(_table_entry(), ch), om, nm)
    assert res["values_checked"] == 2 and res["values_ok"] is True


# -- part B: the table has an id, and the pipeline owns it -------------------------------

def test_the_asset_prompt_names_the_table_id(tmp_path):
    """The request names the table by its id, and the schema asks for that id back.

    A free-text caption is not a key: measured over the three reference runs it fails for
    roughly a third of the entries and, for 60909, for all of them.
    """
    old_doc, new_doc = _docs()
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    block = chapter_assets_block(_chapter(), o_secs, n_secs)
    assert NEW_TABLE_ID in block
    assert CAPTION in block                      # the caption stays, it is readable
    assert '"table_ref"' in ASSET_SCHEMA_DOC and "table id" in ASSET_SCHEMA_DOC

    user, _sel = build_chapter_prompt(_chapter(), OLD_TEXT, NEW_TEXT, o_secs, n_secs)
    assert NEW_TABLE_ID in user


def test_the_table_id_is_pipeline_owned(tmp_path):
    """``table_id`` from a model answer is discarded; the pipeline resolves it itself.

    The model says which table it means (``table_ref``), the pipeline decides which table
    that is. An id taken from the answer would be an assertion nothing checks -- the very
    error class ``section_id`` was in AP-06.
    """
    assert "table_id" in PIPELINE_OWNED_ASSET
    supplied = {"table_id": "erfunden_tab_999", "table_ref": NEW_TABLE_ID}
    assert drop_pipeline_owned(supplied, PIPELINE_OWNED_ASSET) == \
        {"table_id": "erfunden_tab_999"}

    out = _run(tmp_path, _answer(tables=[_table_entry(table_id="erfunden_tab_999")]))
    stored = _stored_table(out)
    assert stored["table_id"] == NEW_TABLE_ID
    assert stored["table"] == CAPTION             # the caption stays readable in reports
    assert table_key(_tables_diff()[0]) == NEW_TABLE_ID
    dropped = [f for f in out["pipeline_feedback"] if f.get("field") == "table_id"]
    assert dropped and dropped[0]["count"] == 1

    # an answer from before AP-31 carries no table_ref: its unique caption still joins
    old_style = {"table": CAPTION, "value_changes": []}
    assert table_key(resolve_table(old_style, _chapter())) == NEW_TABLE_ID


# -- part C: every value change is checked against the cells -----------------------------

def test_a_value_change_is_checked_against_the_cells(tmp_path):
    """Old value on the old side, new value on the new side: both found, entry ok."""
    out = _run(tmp_path, _answer())
    stored = _stored_table(out)
    assert stored["values_checked"] == 2
    assert stored["values_found"] == 2
    assert stored["values_ok"] is True
    assert not [e for e in out["review_queue"] if e.get("asset") == "table"]


def test_a_value_not_in_the_cells_is_flagged(tmp_path):
    """A value in no cell of the named table fails the check and enters the queue."""
    entry = _table_entry(value_changes=["Wirkleistung: 500 kW -> 250 kW"])
    out = _run(tmp_path, _answer(tables=[entry]))
    stored = _stored_table(out)
    assert stored["values_checked"] == 2
    assert stored["values_found"] == 1          # 500 kW is there, 250 kW is not
    assert stored["values_ok"] is False

    queued = [e for e in out["review_queue"] if e.get("asset") == "table"]
    assert len(queued) == 1
    assert "values_ok" in queued[0]["review_reasons"]


def test_a_value_change_without_a_recognisable_value_is_not_a_failure(tmp_path):
    """No number in the entry means nothing to check -- and that is not an error."""
    entry = _table_entry(value_changes=["Zeile für Blindleistung entfällt"])
    out = _run(tmp_path, _answer(tables=[entry]))
    stored = _stored_table(out)
    assert stored["values_checked"] == 0
    assert stored["values_found"] == 0
    assert stored["values_ok"] is True
    assert not [e for e in out["review_queue"] if e.get("asset") == "table"]


def test_an_unjoinable_entry_is_reported_as_unchecked(tmp_path):
    """An entry naming no table of this chapter is unchecked, not wrong.

    Counting it as a failure would measure the join instead of the statement.
    """
    entry = _table_entry(table="Tabelle ohne Caption (Seite 12)",
                         table_ref="Tabelle ohne Caption (Seite 12)")
    out = _run(tmp_path, _answer(tables=[entry]))
    stored = _stored_table(out)
    assert stored["table_id"] is None
    assert stored["values_ok"] is None
    assert stored["values_checked"] is None
    assert stored["values_found"] is None
    assert not [e for e in out["review_queue"] if e.get("asset") == "table"]

    unchecked = [f for f in out["pipeline_feedback"]
                 if f.get("field") == "value_changes"]
    assert unchecked and unchecked[0]["count"] == 1


def test_the_flagged_entry_keeps_its_text(tmp_path):
    """Marked, never corrected: text, status and value_changes come out untouched."""
    entry = _table_entry(value_changes=["Wirkleistung: 500 kW -> 250 kW"])
    out = _run(tmp_path, _answer(tables=[dict(entry)]))
    stored = _stored_table(out)
    for field in ("table", "status", "change", "impact", "value_changes"):
        assert stored[field] == entry[field]


# -- part D: the statistics say what they did not look at --------------------------------

def test_statistics_report_the_unexamined_cell_values(tmp_path):
    """The number of cell values the deterministic stage never examined is written out.

    Including the zero: a 0 in ``kennwert_changes`` must never again be read as "no value
    changed" while twenty values sit in cells beside it.
    """
    old_doc, new_doc = _docs()
    res = build_statistics(old_doc, new_doc, _synopse(), tmp_path / "statistics.json")
    unexamined = res["comparison"]["cell_values_unexamined"]
    assert unexamined == {"old": 2, "new": 2, "total": 4}
    assert json.loads((tmp_path / "statistics.json").read_text(
        encoding="utf-8"))["comparison"]["cell_values_unexamined"] == unexamined

    empty_old, empty_new = _doc(OLD_TABLE_ID, [["a", "b"]], OLD_TEXT), \
        _doc(NEW_TABLE_ID, [["a", "b"]], NEW_TEXT)
    res = build_statistics(empty_old, empty_new, _synopse(), tmp_path / "s2.json")
    assert res["comparison"]["cell_values_unexamined"] == {"old": 0, "new": 0, "total": 0}


def test_kennwert_changes_are_unchanged(tmp_path):
    """Part D adds a number, it does not touch the existing list."""
    syn = _synopse()
    syn["chapters"][0]["changes"][0]["kennwerte"] = {
        "changed": [{"unit": "W", "old": {"raw": "500 kW"}, "new": {"raw": "400 kW"}}],
        "added": [], "removed": [], "n_old": 1, "n_new": 1}
    old_doc, new_doc = _docs()
    res = build_statistics(old_doc, new_doc, syn, tmp_path / "statistics.json")
    assert res["comparison"]["kennwert_changes"] == [
        {"chapter": "1", "mapping_id": "1<1", "old": "500 kW", "new": "400 kW"}]
