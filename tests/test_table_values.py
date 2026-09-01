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

AP-33 adds the grading the measurement asked for: a value that sits in a *neighbouring*
table of the same chapter is something else than a value that sits nowhere (4120: 29 of
30 unconfirmed values), and it is counted separately (``values_elsewhere``) and reported
under its own, weaker reason -- without ever counting as found.

Synthetic material throughout, no standard, no network.
"""
from __future__ import annotations

import json

from normpare.model.document import Table
from normpare.report.stats import build_statistics
from normpare.stages.deutung import (
    ASSET_REVIEW_REASONS,
    ASSET_SCHEMA_DOC,
    PIPELINE_OWNED_ASSET,
    SIDE_BOTH,
    SIDE_NEW,
    SIDE_OLD,
    FixtureProvider,
    asset_review_reasons,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    chapter_assets_block,
    check_asset_values,
    drop_pipeline_owned,
    resolve_table,
    run_deutung,
    table_key,
    value_change_sides,
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


def _table(tid: str, cells: list[list[str]], enriched: bool = True,
           caption: str = CAPTION) -> dict:
    t = {"id": tid, "caption": caption, "cells": cells,
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


def _run(tmp_path, answer: dict, old_doc=None, new_doc=None, syn=None) -> dict:
    """Interpret the one chapter with a frozen answer -- no provider, no network."""
    old_doc = old_doc if old_doc is not None else _docs()[0]
    new_doc = new_doc if new_doc is not None else _docs()[1]
    syn = syn if syn is not None else _synopse()
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


# -- AP-33: a value in the neighbouring table is not a value nowhere ---------------------

SIB_OLD_ID, SIB_NEW_ID = "alt_tab_004", "neu_tab_004"
SIB_CAPTION = "Tabelle 4 – Blindleistung am Netzanschlusspunkt"

#: The sibling pair of the same chapter. "50 Hz" stands in the named table as well (a
#: value found there must not be counted twice), "700 kW" only on the *new* side.
SIB_OLD_CELLS = [["Parameter", "Wert"], ["Blindleistung", "300 kW"], ["Frequenz", "50 Hz"]]
SIB_NEW_CELLS = [["Parameter", "Wert"], ["Blindleistung", "250 kW"],
                 ["Reserve", "700 kW"], ["Frequenz", "50 Hz"]]

#: A table of *another* chapter, carrying the value the entry claims.
FOREIGN_ID = "neu_tab_009"
FOREIGN_CELLS = [["Parameter", "Wert"], ["Blindleistung", "250 kW"]]


def _sibling_docs() -> tuple[dict, dict]:
    """The two documents with a second table in the same chapter."""
    old_doc, new_doc = _docs()
    old_doc["sections"][0]["tables"].append(
        _table(SIB_OLD_ID, SIB_OLD_CELLS, caption=SIB_CAPTION))
    new_doc["sections"][0]["tables"].append(
        _table(SIB_NEW_ID, SIB_NEW_CELLS, caption=SIB_CAPTION))
    return old_doc, new_doc


def _foreign_docs() -> tuple[dict, dict]:
    """The two documents with the claimed value in a table of a *different* chapter."""
    old_doc, new_doc = _docs()
    new_doc["sections"].append(
        {"id": "2", "title": "Anhang", "level": 1, "part": "hauptteil",
         "paragraphs": [{"id": "2.p1", "n0": "Anhang.", "n1": "anhang."}],
         "tables": [_table(FOREIGN_ID, FOREIGN_CELLS, caption="Tabelle 9 – Anhang")],
         "figures": [], "formulas": []})
    return old_doc, new_doc


def _sibling_chapter() -> dict:
    """The chapter of :func:`_sibling_docs`: both table pairs in one ``tables_diff``."""
    ch = _chapter()
    ch["tables_diff"] = _tables_diff() + [
        {"kind": "matched", "old": SIB_OLD_ID, "new": SIB_NEW_ID, "caption": SIB_CAPTION,
         "identical": False, "rows_changed": 1, "confidence": 1.0}]
    return ch


def _check(entry: dict, ch: dict, docs: tuple[dict, dict]) -> dict:
    """Run the value check of one table entry over a whole pair of documents."""
    om, nm = ({t["id"]: t for s in doc["sections"] for t in s.get("tables") or []}
              for doc in docs)
    return check_asset_values(entry, resolve_table(entry, ch), om, nm,
                              ch.get("tables_diff"))


def test_a_value_in_a_sibling_table_is_counted_separately():
    """A value of the neighbouring table is counted, but not as found.

    4120 measured 29 of 30 unconfirmed values this way -- all in 10.2.5, where one
    statement summarises several tables. That is a weaker proof, not a missing one, and
    a list that cannot tell it from an invented number has to be read case by case.
    """
    entry = _table_entry(value_changes=["Zeile für Blindleistung 250 kW entfällt"])
    res = _check(entry, _sibling_chapter(), _sibling_docs())
    assert res["values_checked"] == 1
    assert res["values_found"] == 0
    assert res["values_elsewhere"] == 1


def test_a_value_in_its_own_table_is_not_counted_as_elsewhere():
    """A hit in the named table counts once, even when a sibling carries it too.

    "50 Hz" stands in both tables of the chapter; counting it on both sides would let
    ``values_found + values_elsewhere`` exceed ``values_checked``.
    """
    entry = _table_entry(value_changes=["Frequenz 50 Hz"])
    res = _check(entry, _sibling_chapter(), _sibling_docs())
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == (1, 1, 0)

    both = _check(_table_entry(), _sibling_chapter(), _sibling_docs())
    assert (both["values_checked"], both["values_found"], both["values_elsewhere"]) \
        == (2, 2, 0)


def test_values_ok_ignores_the_sibling_tables():
    """``values_ok`` keeps its meaning: every recognised value in the *named* table.

    The grading grows beside it, never inside it -- a sibling hit counted as a hit would
    erase the difference between "checked" and "found somewhere".
    """
    entry = _table_entry(value_changes=["Zeile für Blindleistung 250 kW entfällt"])
    res = _check(entry, _sibling_chapter(), _sibling_docs())
    assert res["values_ok"] is False
    assert res["values_ok"] == (res["values_found"] == res["values_checked"])
    assert asset_review_reasons({**entry, **res}) == ["values_in_other_table"]


def test_the_weaker_reason_fires_only_when_everything_is_accounted_for():
    """One value nowhere in the chapter, and the entry is the serious case again."""
    entry = _table_entry(value_changes=["Blindleistung: 999 kW -> 250 kW"])
    res = _check(entry, _sibling_chapter(), _sibling_docs())
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == (2, 0, 1)
    assert asset_review_reasons({**entry, **res}) == ["values_ok"]


def test_the_reasons_are_mutually_exclusive():
    """No entry ever carries both value reasons, whatever the mixture of hits."""
    assert ASSET_REVIEW_REASONS.index("values_in_other_table") > \
        ASSET_REVIEW_REASONS.index("values_ok")
    cases = ["Wirkleistung: 500 kW -> 400 kW",           # both in the named table
             "Zeile für Blindleistung 250 kW entfällt",  # only in the sibling
             "Blindleistung: 999 kW -> 250 kW",          # one nowhere, one in the sibling
             "Wirkleistung: 500 kW -> 999 kW",           # one named, one nowhere
             "Zeile entfällt"]                           # nothing to check
    for text in cases:
        entry = _table_entry(value_changes=[text])
        record = {**entry, **_check(entry, _sibling_chapter(), _sibling_docs())}
        reasons = set(asset_review_reasons(record))
        assert not {"values_ok", "values_in_other_table"} <= reasons, text


def test_the_sibling_search_stays_inside_the_chapter():
    """A table of another chapter is not a sibling, however well the value fits.

    The neighbourhood is the ``tables_diff`` of this mapping record. Searching the whole
    document would find every percentage somewhere and would say nothing.
    """
    entry = _table_entry(value_changes=["Zeile für Blindleistung 250 kW entfällt"])
    res = _check(entry, _chapter(), _foreign_docs())
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == (1, 0, 0)
    assert asset_review_reasons({**entry, **res}) == ["values_ok"]


def test_an_unjoinable_entry_reports_none_for_elsewhere(tmp_path):
    """Without a table there is no neighbourhood either: unchecked, not zero."""
    entry = _table_entry(table="Tabelle ohne Caption (Seite 12)",
                         table_ref="Tabelle ohne Caption (Seite 12)")
    out = _run(tmp_path, _answer(tables=[entry]))
    stored = _stored_table(out)
    assert stored["values_elsewhere"] is None
    assert stored["values_ok"] is None
    assert not [e for e in out["review_queue"] if e.get("asset") == "table"]


def test_the_side_choice_still_applies():
    """Old value on the old side, new value on the new -- across the siblings as well.

    "700 kW" stands in the *new* sibling only. Claimed as the old value it is not there,
    and an entry without an arrow says nothing about its edition, so both sides count.
    """
    entry = _table_entry(value_changes=["Reserve: 700 kW -> 400 kW"])
    res = _check(entry, _sibling_chapter(), _sibling_docs())
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == (2, 1, 0)

    no_arrow = _table_entry(value_changes=["Reserve 700 kW"])
    res = _check(no_arrow, _sibling_chapter(), _sibling_docs())
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == (1, 0, 1)


def test_the_feedback_names_the_sibling_count(tmp_path):
    """The run says how many values sat in a neighbouring table -- zero included.

    A number that only appears in the failure case leaves the good case unmeasured
    (AP-20, AP-28): without the zero nobody can tell "no summaries" from "not counted".
    """
    out = _run(tmp_path, _answer())
    line = [f for f in out["pipeline_feedback"] if f.get("field") == "values_elsewhere"]
    assert len(line) == 1 and line[0]["count"] == 0

    syn = _synopse()
    syn["chapters"][0] = _sibling_chapter()
    entry = _table_entry(value_changes=["Zeile für Blindleistung 250 kW entfällt"])
    old_doc, new_doc = _sibling_docs()
    out = _run(tmp_path, _answer(tables=[entry]), old_doc, new_doc, syn)

    assert _stored_table(out)["values_elsewhere"] == 1
    line = [f for f in out["pipeline_feedback"] if f.get("field") == "values_elsewhere"]
    assert len(line) == 1 and line[0]["count"] == 1
    assert "1" in line[0]["finding"]

    queued = [e for e in out["review_queue"] if e.get("asset") == "table"]
    assert len(queued) == 1
    assert queued[0]["review_reasons"] == ["values_in_other_table"]


def test_values_elsewhere_is_pipeline_owned():
    """The count is the pipeline's, like the three numbers it stands beside."""
    assert "values_elsewhere" in PIPELINE_OWNED_ASSET
    assert drop_pipeline_owned({"values_elsewhere": 7}, PIPELINE_OWNED_ASSET) == \
        {"values_elsewhere": 7}


# -- AP-36 part 1: a table without a counterpart has one side to search on ---------------

ONE_ID = {"new": "neu_tab_007", "old": "alt_tab_007"}
ONE_CAPTION = "Tabelle 7 – Impedanzwinkelfaktor"
#: Shaped like 4110 B.9.9 / tab_097: both numbers of the entry stand in the cells of the
#: one edition this table has -- the arrow between them assigns, it does not date.
ONE_CELLS = [["Parameter", "Wert"], ["X/R", "0,2"], ["kXR", "0,4"]]
ONE_ENTRY = "X/R < 0,2 -> kXR = 0,4"


def _one_sided_docs(side: str) -> tuple[dict, dict]:
    """The two documents with the table on ``side`` only -- the other side has none."""
    docs = {"old": _doc(ONE_ID["old"], ONE_CELLS, OLD_TEXT),
            "new": _doc(ONE_ID["new"], ONE_CELLS, NEW_TEXT)}
    empty = _doc(ONE_ID["old" if side == "new" else "new"], ONE_CELLS, OLD_TEXT)
    empty["sections"][0]["tables"] = []
    return ((empty, docs["new"]) if side == "new" else (docs["old"], empty))


#: What ``tables_diff`` calls a table that has only the one side.
ONE_KIND = {"new": "new", "old": "removed"}


def _one_sided_chapter(side: str, extra: list[dict] | None = None) -> dict:
    """The chapter of :func:`_one_sided_docs`: one ``tables_diff`` entry, one side."""
    ch = _chapter()
    ch["tables_diff"] = [{"kind": ONE_KIND[side], side: ONE_ID[side],
                          "caption": ONE_CAPTION}] + (extra or [])
    return ch


def _one_sided_entry(side: str = "new", **over) -> dict:
    d = {"table": ONE_CAPTION, "table_ref": ONE_ID[side], "value_changes": [ONE_ENTRY]}
    d.update(over)
    return _table_entry(**d)


def test_a_new_table_searches_both_arrow_sides_on_its_own_side():
    """A table added by the new edition has no old side to hold the left half against.

    ``value_change_sides`` charges the left half of an arrow to the old edition, and for
    a ``kind: "new"`` table that edition is the empty set: every value left of the arrow
    counts as missing however plainly it stands in the cells. Measured on 4110 B.9.9 /
    tab_097 (30 checked / 10 found, all 20 missing ones in the cells of the new side).
    """
    entry = _one_sided_entry()
    res = _check(entry, _one_sided_chapter("new"), _one_sided_docs("new"))
    assert (res["values_checked"], res["values_found"]) == (2, 2)
    assert res["values_ok"] is True
    assert asset_review_reasons({**entry, **res}) == []


def test_a_removed_table_searches_both_arrow_sides_on_its_own_side():
    """The mirror case: a table the new edition dropped has only its old side."""
    entry = _one_sided_entry(side="old")
    res = _check(entry, _one_sided_chapter("old"), _one_sided_docs("old"))
    assert (res["values_checked"], res["values_found"]) == (2, 2)
    assert res["values_ok"] is True


def test_a_two_sided_table_keeps_its_side_discipline():
    """A pair keeps both haystacks apart: left is the old edition, right the new one.

    The substitution is for the *missing* side only. Where both sides exist, an entry
    that names them the wrong way round still finds nothing -- that is the whole point of
    splitting at the arrow.
    """
    right = _table_entry(value_changes=["Wirkleistung: 500 kW -> 400 kW"])
    res = _check(right, _chapter(), _docs())
    assert (res["values_checked"], res["values_found"]) == (2, 2)

    swapped = _table_entry(value_changes=["Wirkleistung: 400 kW -> 500 kW"])
    res = _check(swapped, _chapter(), _docs())
    assert (res["values_checked"], res["values_found"]) == (2, 0)


def test_an_entry_without_an_arrow_is_unchanged():
    """Without an arrow both editions are searched, on a pair and on a single table."""
    both = _table_entry(value_changes=["Frequenz 50 Hz"])
    res = _check(both, _chapter(), _docs())
    assert (res["values_checked"], res["values_found"]) == (1, 1)

    single = _one_sided_entry(value_changes=["Zeile für kXR = 0,4 entfällt"])
    res = _check(single, _one_sided_chapter("new"), _one_sided_docs("new"))
    assert (res["values_checked"], res["values_found"]) == (1, 1)

    absent = _one_sided_entry(value_changes=["Zeile für kXR = 0,9 entfällt"])
    res = _check(absent, _one_sided_chapter("new"), _one_sided_docs("new"))
    assert (res["values_checked"], res["values_found"]) == (1, 0)


def test_value_change_sides_is_untouched():
    """The split at the arrow is right; what the halves were held against was not.

    Pinned as a parity table: the same pairs as before AP-36, for every arrow shape and
    for the entry without one.
    """
    assert value_change_sides("500 kW -> 400 kW") == [(SIDE_OLD, "500 kW"),
                                                      (SIDE_NEW, "400 kW")]
    for arrow in ("->", "-->", "=>", "==>", "→", "➔", "➝", "»"):
        assert value_change_sides(f"5 % {arrow} 4 %") == [(SIDE_OLD, "5 %"),
                                                          (SIDE_NEW, "4 %")]
    assert value_change_sides("Zeile für 0,85 Un entfernt") == \
        [(SIDE_BOTH, "Zeile für 0,85 Un entfernt")]
    assert value_change_sides("") == [(SIDE_BOTH, "")]
    # only the first arrow splits, and it splits into exactly two halves
    assert value_change_sides("1 -> 2 -> 3") == [(SIDE_OLD, "1"), (SIDE_NEW, "2 -> 3")]


def test_a_one_sided_sibling_is_searched_on_its_own_side():
    """The neighbourhood follows the same rule, table by table.

    ``values_elsewhere`` reads the *other* tables of the record. A neighbour without a
    counterpart contributes to one side only, and charging the left half of an arrow to
    its empty side would lose the weaker proof for the same reason the named table lost
    the stronger one.
    """
    docs = _one_sided_docs("new")
    docs[1]["sections"][0]["tables"].append(
        _table(SIB_NEW_ID, [["Parameter", "Wert"], ["kXR", "0,7"]], caption=SIB_CAPTION))
    ch = _one_sided_chapter("new", extra=[{"kind": "new", "new": SIB_NEW_ID,
                                           "caption": SIB_CAPTION}])
    entry = _one_sided_entry(value_changes=["kXR 0,7 -> 0,4"])
    res = _check(entry, ch, docs)
    assert (res["values_checked"], res["values_found"], res["values_elsewhere"]) == \
        (2, 1, 1)
