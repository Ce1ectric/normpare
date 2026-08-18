"""What the outputs make of a table that changed chapters (AP-23).

``tables_diff`` gained two kinds, ``moved_away`` at the old place and ``moved_in`` at the
new one. Everything that reads ``tables_diff`` decides by ``kind``, so a consumer that
does not know the two new values silently drops the table: the annotated HTML would show
no table at all where the moved one now stands, and the interpretation prompt would not
see its cells. Both are content, not formatting -- these tests pin them down.

No network, no LLM: the material is a synthetic four-row certificate table.
"""
from __future__ import annotations

from normpare.report.html import _render_tablediff
from normpare.stages.deutung import chapter_assets_block

CELLS = [["Anlagenzertifikat B", "", ""],
         ["Pruefgegenstand", "Nachweis", "Beiblatt"],
         ["Modellvalidierung", "Simulationsbericht", "Anlage 1"],
         ["Konformitaetsnachweis", "Pruefbericht", "Anlage 2"]]
OLD = {"id": "o_cert", "caption": "", "n_rows": 4, "n_cols": 3, "cells": CELLS}
NEW = dict(OLD, id="n_cert", caption="Tabelle 40 - Bewertungsumfang")

MOVED_IN = {"kind": "moved_in", "old": "o_cert", "new": "n_cert",
            "caption": "Tabelle 40 - Bewertungsumfang", "rows_changed": 1,
            "identical": False, "confidence": 1.0, "moved_from_chapter": "11.4.21<11.4.21"}
MOVED_AWAY = {"kind": "moved_away", "old": "o_cert", "new": "n_cert", "caption": "",
              "rows_changed": 1, "identical": False, "confidence": 1.0,
              "moved_to_chapter": "11.4.24<11.4.24"}


def test_a_moved_table_is_rendered_in_the_html():
    """At its new place the moved table is shown like a paired one -- with the old and the
    new side next to each other -- and it says where it came from."""
    out = _render_tablediff(MOVED_IN, {"o_cert": OLD}, {"n_cert": NEW})

    assert "Modellvalidierung" in out          # the table itself is there
    assert "11.4.21" in out                    # and it names its origin


def test_a_moved_table_reaches_the_interpretation_prompt():
    """Both ends carry their cells into the prompt: the new edition at the new place, the
    old one at the old place. Before AP-23 the same two tables were in the prompt as an
    addition and a deletion -- the move must not make them disappear."""
    o_secs = {"11.4.21": {"id": "11.4.21", "tables": [OLD], "figures": []}}
    n_secs = {"11.4.24": {"id": "11.4.24", "tables": [NEW], "figures": []}}

    into = chapter_assets_block({"tables_diff": [MOVED_IN], "old_ids": [], "new_ids":
                                 ["11.4.24"]}, o_secs, n_secs)
    assert "Modellvalidierung" in into
    assert "11.4.21" in into

    away = chapter_assets_block({"tables_diff": [MOVED_AWAY], "old_ids": ["11.4.21"],
                                 "new_ids": []}, o_secs, n_secs)
    assert "Modellvalidierung" in away
    assert "11.4.24" in away
