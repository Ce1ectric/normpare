"""Tests for what the coverage counts, and for axis B without a counterpart (AP-28).

Two findings from the three DeepSeek runs of 2026-08-22:

* ``coverage.n_interpreted`` counted the changes a prompt was *shown*, not the
  interpretations that came *back*. In the 60909 run 77 of 1897 changes never got one --
  no duplicate index, no collision, simply no answer -- and the report said
  "1897 von 1897 (100,0 %)". Those 77 appear in no component view and in no CSV row.
* ``semantic_status`` is the only axis that goes missing, and it goes missing where a
  change has no counterpart: 25.5 % of ``new`` and 21.1 % of ``removed`` in the 4110 run.
  The axis says what happens to the statement, and a new statement has no predecessor the
  vocabulary could be read against -- the rule was missing from the field description.

No test here touches the network; the answers come from a fake provider.
"""
from __future__ import annotations

from normpare.report.changes_csv import COVERAGE_NOTE_SUFFIX, build_changes_csv
from normpare.report.component_view import render_component_view
from normpare.stages.deutung import (
    CHAPTER_SCHEMA_DOC,
    SEMANTIC_STATUS,
    check_axes,
    run_deutung,
)

# -- material ------------------------------------------------------------------------------


def _changes(n: int, **extra) -> list[dict]:
    return [{"kind": "changed",
             "old_text": f"Der Nachweis ist alle {i} Jahre zu führen.",
             "new_text": f"Der Nachweis ist alle {i} Monate zu führen.", **extra}
            for i in range(n)]


def _chapter(n: int, **extra) -> dict:
    return {"old_id": "11.2", "new_id": "11.2", "mapping_id": "11.2+11.2",
            "title": "Nachweisverfahren", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": ["11.2"], "new_ids": ["11.2"],
            "tables_diff": [], "changes": _changes(n), **extra}


def _cosmetic_chapter(n: int) -> dict:
    """A chapter the interpretation stage skips: every change is semantically equal."""
    return {"old_id": "3.1", "new_id": "3.1", "mapping_id": "3.1+3.1",
            "title": "Begriffe", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": ["3.1"], "new_ids": ["3.1"],
            "tables_diff": [], "changes": _changes(n, semantic_equal=True)}


def _docs() -> tuple[dict, dict]:
    def doc(unit: str) -> dict:
        return {"sections": [{
            "id": "11.2", "title": "Nachweisverfahren", "tables": [], "figures": [],
            "paragraphs": [{"id": "11.2.p1",
                            "n0": f"Der Nachweis ist alle {i} {unit} zu führen.",
                            "n1": f"Der Nachweis ist alle {i} {unit} zu führen."}
                           for i in range(3)]}]}

    return doc("Jahre"), doc("Monate")


def _answer(indices, **extra) -> dict:
    return {"section_id": "11.2", "summary_old": "Jährlich.", "summary_new": "Monatlich.",
            "change_overview": "Verkürzte Fristen.", "training_relevance": "high",
            "keywords": [], "practical_note": "",
            "interpretations": [{"change_index": i, "change": f"Deutung {i}",
                                 "semantic_status": "clarified",
                                 "normative_direction": "unchanged",
                                 "affected_components": ["procedure"],
                                 "evidence": f"Der Nachweis ist alle {i} Monate zu führen.",
                                 "impact": "", "confidence": 0.9} for i in indices],
            **extra}


class _OneAnswer:
    """Returns the same answer for every prompt of the run."""

    live = True

    def __init__(self, answer: dict | None):
        self.answer = answer
        self.outcomes: dict[str, str] = {}

    def resolve(self, items):
        for tag, _prompt in items:
            self.outcomes[tag] = "ok" if self.answer is not None else "unparsable"
        return {tag: self.answer for tag, _prompt in items}


def _run(tmp_path, chapters: list[dict], answer: dict | None, capsys=None):
    old, new = _docs()
    out = run_deutung({"pair": "test", "chapters": chapters}, old, new, tmp_path,
                      tmp_path / "out", model="test-model",
                      deutung_provider=_OneAnswer(answer))
    return out, (capsys.readouterr().out if capsys else "")


# -- 1..4: counting what came back ----------------------------------------------------------

def test_coverage_counts_returned_interpretations(tmp_path):
    """Ten changes go into the prompt, seven interpretations come back: seven.

    The old count read the selection the prompt was built from, which is a property of
    the request, not of the answer. As long as a model answers about every change the two
    are equal -- DeepSeek does not always, and then the run reports full coverage over
    missing content.
    """
    out, _ = _run(tmp_path, [_chapter(10)], _answer(range(7)))

    assert out["coverage"]["n_changes"] == 10          # shown to a prompt
    assert out["coverage"]["n_interpreted"] == 7       # came back
    chapter = out["chapters"][0]
    assert sorted(chapter["_changes_interpreted"]) == list(range(7))
    assert len(chapter["interpretations"]) == 7


def test_unanswered_changes_are_counted(tmp_path):
    """The three changes without an answer are their own number, not a rounding loss."""
    out, _ = _run(tmp_path, [_chapter(10)], _answer(range(7)))
    coverage = out["coverage"]

    assert coverage["n_unanswered"] == 3
    assert coverage["n_unanswered"] == coverage["n_changes"] - coverage["n_interpreted"]
    # ... and machine-readable, in the same feedback entry as the rest
    entry = next(fb for fb in out["pipeline_feedback"]
                 if fb.get("phase") == "deutung" and fb.get("field") == "coverage")
    assert entry["n_unanswered"] == 3
    assert entry["n_interpreted"] == 7
    assert "3" in entry["finding"]


def test_unanswered_chapters_are_named(tmp_path, capsys):
    """Which chapters lost changes, with how many -- all 77 of the 60909 run sat in one.

    The console also tells apart the changes of the comparison from those a prompt was
    shown: a chapter whose changes are all semantically equal is skipped on purpose, and
    counting its changes as unanswered would blame the model for a decision of the stage.
    """
    out, printed = _run(tmp_path, [_chapter(10), _cosmetic_chapter(2)],
                        _answer(range(7)), capsys)

    assert "Änderungen: 12, vorgelegt 10, gedeutet 7" in printed
    assert "ohne Antwort: 3" in printed
    assert "11.2+11.2 (3)" in printed
    assert out["coverage"]["n_changes_total"] == 12


def test_full_coverage_reports_zero_unanswered(tmp_path, capsys):
    """Also at zero. A number that only shows up when it is bad leaves the good case
    unmeasured -- which is how the silent 40-change cap survived two production runs."""
    _out, printed = _run(tmp_path, [_chapter(10)], _answer(range(10)), capsys)

    assert "Änderungen: 10, vorgelegt 10, gedeutet 10 (100,0 %)" in printed
    assert "ohne Antwort: 0" in printed


def test_the_deliverable_note_uses_the_returned_count(tmp_path):
    """The head note of both AP-17 deliverables counts interpretations, not requests."""
    coverage = {"n_changes": 10, "n_changes_total": 12, "n_interpreted": 7,
                "n_unanswered": 3, "n_split_chapters": 0, "n_extra_requests": 0,
                "n_collisions": 0, "n_repaired": 0,
                "incomplete": [{"section_id": "11.2", "mapping_id": "11.2+11.2",
                                "n_changes": 10, "n_interpreted": 7}]}
    text = render_component_view([], "test alt<->neu", "2026-08-23", coverage=coverage)

    assert "unvollständig" in text.lower()
    assert "7" in text and "12" in text
    assert "58,3 %" in text                       # 7 of 12, not 10 of 10

    csv_path = tmp_path / "Aenderungen_run.csv"
    build_changes_csv({"chapters": []}, {"chapters": [], "coverage": coverage}, csv_path)
    note = csv_path.with_name(csv_path.stem + COVERAGE_NOTE_SUFFIX)
    headline = note.read_text(encoding="utf-8").splitlines()[0]
    assert "7" in headline and "12" in headline and "58,3 %" in headline


# -- 5..8: axis B for a change without a counterpart -----------------------------------------

def test_the_schema_states_the_rule_for_new_and_removed():
    """The field description says what axis B means when there is no counterpart.

    Without the rule the axis is undecidable for a pure addition or a pure deletion, and
    both interpreters so far answered it differently: the 2026-08-17 run forced
    ``narrowed`` onto every deletion, DeepSeek left the field empty in a quarter of the
    ``new`` changes. The rule belongs where the model reads it.
    """
    doc = CHAPTER_SCHEMA_DOC
    line = next(l for l in doc.splitlines() if '"semantic_status"' in l)

    assert "counterpart" in line
    # added text extends the body of statements, dropped text narrows it
    assert "extended" in line and "narrowed" in line and "replaced" in line
    lower = line.lower()
    assert "added" in lower and ("dropped" in lower or "removed" in lower)


def test_a_missing_semantic_status_is_counted(tmp_path):
    """A missing axis value stays missing, is counted and reported -- never invented."""
    answer = _answer(range(3))
    del answer["interpretations"][1]["semantic_status"]
    out, _ = _run(tmp_path, [_chapter(3)], answer)

    interpretations = out["chapters"][0]["interpretations"]
    assert interpretations[1]["semantic_status"] is None
    assert interpretations[0]["semantic_status"] == "clarified"
    entry = next(fb for fb in out["pipeline_feedback"]
                 if fb.get("field") == "semantic_status")
    assert entry["reason"] == "missing"
    assert entry["count"] == 1
    assert "missing" in entry["finding"]

    # the same on the pure function, for both changes without a counterpart
    fields, bad = check_axes({"normative_direction": "unchanged",
                              "affected_components": ["scope"]}, {"kind": "new"})
    assert fields["semantic_status"] is None
    assert [v["reason"] for v in bad if v["field"] == "semantic_status"] == ["missing"]


def test_the_vocabulary_is_unchanged():
    """The rule is a rule, not a new value: axis A already carries added/removed, and a
    second place for the same statement is the category error ENT-01 removed."""
    assert SEMANTIC_STATUS == ["equivalent", "clarified", "extended", "narrowed",
                               "replaced", "contradictory", "indeterminate"]
    line = next(l for l in CHAPTER_SCHEMA_DOC.splitlines() if '"semantic_status"' in l)
    offered = line.split('"')[3].split(" -- ")[0]
    assert offered.split("|") == SEMANTIC_STATUS
