"""Tests for the four consistency checks of AP-29 -- the axes against what the pipeline knows.

Four findings, all measured offline over the three finished runs of 2026-08-27
(``arbeit/4110_v3``, ``arbeit/4120_v3``, ``arbeit/60909_v2``):

1. **The same move is interpreted twice, differently.** A move is one event with two
   change records, and the pipeline knows the pair deterministically over
   ``moved_away.moved_to`` -> ``moved_in.new_ids[0]``. Of the paired interpretations
   66 % / 88 % / 54 % disagree on axis B, almost always with the same signature: the
   ``moved_away`` side says ``replaced``, the ``moved_in`` side says ``equivalent``.
   The same paragraph cannot be both.
1a. **Seven pointers of the 60909 run hit a record the pipeline calls ``new``** -- the
   old side says "moved to X", the new side says "X is new", and both are counted today.
2. **Axis B contradicts axis A**: 93 / 49 / 58 interpretations answer ``equivalent`` for
   a change that has no counterpart at all (``new`` or ``removed``).
3. **Axis C contradicts the deterministic modality**: 78 / 88 / 39 changes carry a modal
   sentence and are called ``not_applicable`` -- non-normative.
4. **The free text names the successor while the axis reports a deletion**: 12 cases over
   the three runs, every one of them additionally ``relaxed``.

Every check **marks, never corrects** (the AP-07 pattern): the reported value stays where
it is and a flag joins it. Whoever corrects measures the correction instead of the model.

No test here touches the network; every answer comes from a fake provider.
"""
from __future__ import annotations

import copy

from normpare.stages.deutung import (
    CHAPTER_SCHEMA_DOC,
    NORMATIVE_DIRECTIONS,
    REVIEW_REASONS,
    SEMANTIC_STATUS,
    check_axes,
    check_consistency,
    consistency_feedback,
    move_pairs,
    run_deutung,
    successor_named,
)

# -- material ------------------------------------------------------------------------------

MOVED_TEXT = "Die Messeinrichtung muss jährlich geprüft werden."


def _away(new_id: str | None, **extra) -> dict:
    """A ``moved_away`` change record; ``new_id`` is the pointer, ``None`` leaves it out."""
    rec = {"kind": "moved_away", "confidence": 1.0, "old_ids": ["3.1.22.p1"],
           "new_ids": [], "old_text": MOVED_TEXT, "new_text": None,
           "modality": {"old": "muss"}}
    if new_id:
        rec["moved_to"] = new_id
    rec.update(extra)
    return rec


def _into(new_id: str, **extra) -> dict:
    rec = {"kind": "moved_in", "confidence": 1.0, "old_ids": [], "new_ids": [new_id],
           "old_text": None, "new_text": MOVED_TEXT, "modality": {"new": "muss"},
           "moved_from": "3.1.22.p1"}
    rec.update(extra)
    return rec


def _chapter(mapping_id: str, changes: list[dict], **extra) -> dict:
    old, new = (mapping_id.split("<") + [mapping_id])[:2]
    return {"old_id": old, "new_id": new, "mapping_id": mapping_id,
            "title": "Begriffe", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": [old], "new_ids": [new],
            "tables_diff": [], "changes": changes, **extra}


def _synopse(chapters: list[dict]) -> dict:
    return {"pair": "alt <-> neu", "chapters": chapters}


def _interpretation(index: int, **extra) -> dict:
    """One interpretation as it stands in ``deutung.json`` after the axes were checked."""
    return {"change_index": index, "semantic_status": "equivalent",
            "normative_direction": "unchanged", "affected_components": ["procedure"],
            "structural_operation": "moved", "change": "Der Absatz wurde verschoben.",
            "impact": "", "evidence": MOVED_TEXT, "confidence": "high",
            "evidence_ok": True, **extra}


def _deutung_chapter(mapping_id: str, interpretations: list[dict]) -> dict:
    return {"section_id": mapping_id.split("<")[-1], "mapping_id": mapping_id,
            "summary_old": "", "summary_new": "", "change_overview": "",
            "interpretations": interpretations, "_source": "llm"}


def _move_run(away_extra: dict, into_extra: dict) -> tuple[list[dict], dict]:
    """One move, one interpretation on each side -- the material of findings 1 and 1a."""
    synopse = _synopse([
        _chapter("3.1.22<3.1", [_away("3.1.p91")]),
        _chapter("3.1<3.1", [_into("3.1.p91")]),
    ])
    chapters = [
        _deutung_chapter("3.1.22<3.1", [_interpretation(0, **away_extra)]),
        _deutung_chapter("3.1<3.1", [_interpretation(0, **into_extra)]),
    ]
    return chapters, synopse


# -- 1..3: joining the pair over the pointer ------------------------------------------------

def test_move_pairs_join_over_the_pointer():
    """``moved_to`` -> ``new_ids[0]`` joins exactly the pairs the pipeline can prove.

    No similarity, no model: the aligner wrote the pointer when it made the move, and it
    is the only evidence that two change records describe one event.
    """
    synopse = _synopse([
        _chapter("3.1.22<3.1", [_away("3.1.p91"), _away("3.1.p92")]),
        _chapter("3.1<3.1", [_into("3.1.p91"), _into("3.1.p92")]),
    ])

    joined = move_pairs(synopse)

    assert len(joined["pairs"]) == 2
    assert joined["unpaired"] == [] and joined["dangling"] == []
    first = joined["pairs"][0]
    assert first["away"] == {"mapping_id": "3.1.22<3.1", "change_index": 0}
    assert first["into"] == {"mapping_id": "3.1<3.1", "change_index": 0}
    assert first["new_id"] == "3.1.p91"
    assert joined["pairs"][1]["into"]["change_index"] == 1


def test_a_move_without_a_pointer_is_not_paired():
    """A block continuation (AP-26) knows the chapter, not the paragraph -- and is left alone.

    ``moved_to_chapter`` with ``via: "block"`` and confidence 0.0 is context, not evidence.
    Pairing it would compare two interpretations that may well be about different
    paragraphs, so it is counted as unpaired and never checked.
    """
    synopse = _synopse([
        _chapter("3.1.22<3.1", [_away(None, moved_to_chapter="3.1<3.1", via="block",
                                      confidence=0.0)]),
        _chapter("3.1<3.1", [_into("3.1.p91")]),
    ])

    joined = move_pairs(synopse)

    assert joined["pairs"] == []
    assert joined["unpaired"] == [{"mapping_id": "3.1.22<3.1", "change_index": 0,
                                   "moved_to_chapter": "3.1<3.1", "via": "block"}]


def test_a_pointer_into_a_new_record_is_reported():
    """The old side says "moved to X", the new side calls X new -- seven times in 60909.

    Both cannot be true, and both are counted today: once as a move, once as an addition.
    The case is a finding of the deterministic stage, so it is reported and not quietly
    skipped.
    """
    synopse = _synopse([
        _chapter("7<7.2", [_away("A.p13")]),
        _chapter("A<A", [{"kind": "new", "confidence": 1.0, "old_ids": [],
                          "new_ids": ["A.p13"], "old_text": None,
                          "new_text": MOVED_TEXT, "modality": {"new": "muss"}}]),
    ])

    joined = move_pairs(synopse)

    assert joined["pairs"] == []
    assert joined["dangling"] == [{"mapping_id": "7<7.2", "change_index": 0,
                                   "moved_to": "A.p13", "kinds": ["new"]}]


# -- 4..6: what the pair check writes -------------------------------------------------------

def test_disagreeing_partners_are_flagged_on_both_sides():
    """``replaced`` here, ``equivalent`` there: both records carry the other side's value.

    Both sides, not one: which of the two is right is a question for the schema rule, not
    for a check that has no text in front of it.
    """
    chapters, synopse = _move_run({"semantic_status": "replaced"},
                                  {"semantic_status": "equivalent"})

    report = check_consistency(chapters, synopse)

    away = chapters[0]["interpretations"][0]
    into = chapters[1]["interpretations"][0]
    assert away["axis_partner_disagreement"] == [
        {"axis": "semantic_status", "value": "replaced", "partner_value": "equivalent",
         "partner_mapping_id": "3.1<3.1", "partner_change_index": 0}]
    assert into["axis_partner_disagreement"] == [
        {"axis": "semantic_status", "value": "equivalent", "partner_value": "replaced",
         "partner_mapping_id": "3.1.22<3.1", "partner_change_index": 0}]
    assert report["n_pairs"] == 1
    assert report["n_partner_disagreement"] == 1
    assert report["by_axis"]["semantic_status"] == 1


def test_agreeing_partners_carry_no_flag():
    """Same values on both sides: not a field is added, the record is what it was."""
    chapters, synopse = _move_run({"semantic_status": "equivalent"},
                                  {"semantic_status": "equivalent"})
    before = copy.deepcopy(chapters)

    report = check_consistency(chapters, synopse)

    assert chapters == before
    assert report["n_pairs"] == 1 and report["n_partner_disagreement"] == 0


def test_axis_c_disagreement_is_flagged_too():
    """Axis C is checked exactly like axis B -- ``not_applicable`` against ``unchanged``
    is the second signature of the finding (24 / 36 / 6 cases)."""
    chapters, synopse = _move_run({"normative_direction": "not_applicable"},
                                  {"normative_direction": "unchanged"})

    report = check_consistency(chapters, synopse)

    flagged = chapters[0]["interpretations"][0]["axis_partner_disagreement"]
    assert flagged == [{"axis": "normative_direction", "value": "not_applicable",
                        "partner_value": "unchanged", "partner_mapping_id": "3.1<3.1",
                        "partner_change_index": 0}]
    assert report["by_axis"]["normative_direction"] == 1
    # ... and the value itself is untouched: marked, not corrected
    assert chapters[0]["interpretations"][0]["normative_direction"] == "not_applicable"


# -- 7..8: the rule in the schema, and nothing else ------------------------------------------

def test_the_schema_states_the_rule_for_moves():
    """The field description says what axis B means for a move (Christian, 2026-08-27).

    Axis B describes the text, axis A the place. Unchanged text of a move is
    ``equivalent``; ``replaced`` stays with the change whose counterpart is *not* linked
    by a move record.
    """
    line = next(x for x in CHAPTER_SCHEMA_DOC.splitlines() if '"semantic_status"' in x)
    lower = line.lower()

    assert "moved" in lower and "place" in lower
    assert "equivalent" in lower and "replaced" in lower


def test_the_vocabulary_is_unchanged():
    """A rule, not a value: no new entry on axis B or C and no new axis field."""
    assert SEMANTIC_STATUS == ["equivalent", "clarified", "extended", "narrowed",
                               "replaced", "contradictory", "indeterminate"]
    assert NORMATIVE_DIRECTIONS == ["tightened", "relaxed", "unchanged", "not_applicable",
                                    "indeterminate"]
    fields, _bad = check_axes({"semantic_status": "equivalent",
                               "normative_direction": "unchanged",
                               "affected_components": ["procedure"]},
                              {"kind": "moved_in"})
    assert set(fields) == {"structural_operation", "semantic_status",
                           "normative_direction", "affected_components",
                           "indeterminate_reason"}


# -- 9..10: axis B against axis A ------------------------------------------------------------

def test_equivalent_without_a_counterpart_is_flagged():
    """``equivalent`` on a pure addition or deletion: 200 cases over the three runs.

    AP-28 wrote the rule into the field description; the description alone did not close
    the gap. What axis A knows deterministically has to be checked against axis B.
    """
    synopse = _synopse([_chapter("5<5", [
        {"kind": "new", "confidence": 1.0, "old_ids": [], "new_ids": ["5.p1"],
         "old_text": None, "new_text": MOVED_TEXT, "modality": {"new": "muss"}},
        {"kind": "removed", "confidence": 1.0, "old_ids": ["5.p9"], "new_ids": [],
         "old_text": MOVED_TEXT, "new_text": None, "modality": {"old": "muss"}}])]
    )
    chapters = [_deutung_chapter("5<5", [
        _interpretation(0, semantic_status="equivalent", structural_operation="added",
                        normative_direction="tightened"),
        _interpretation(1, semantic_status="equivalent", structural_operation="removed",
                        normative_direction="relaxed")])]

    report = check_consistency(chapters, synopse)

    assert all(d["axis_b_contradicts_a"] for d in chapters[0]["interpretations"])
    assert report["n_axis_b_contradicts_a"] == 2
    entry = next(e for e in consistency_feedback(report)
                 if e["field"] == "axis_b_contradicts_a")
    assert entry["count"] == 2 and "equivalent" in entry["finding"]


def test_the_flagged_value_is_kept():
    """Marked, not replaced -- the value is still readable, and still the model's.

    This is the AP-07 pattern (``change_index_disputed``), not the AP-14 one (discard on
    a vocabulary violation): there the value is unreadable, here it is readable and
    merely disputed.
    """
    synopse = _synopse([_chapter("5<5", [
        {"kind": "new", "confidence": 1.0, "old_ids": [], "new_ids": ["5.p1"],
         "old_text": None, "new_text": MOVED_TEXT, "modality": {"new": "muss"}}])])
    chapters = [_deutung_chapter("5<5", [
        _interpretation(0, semantic_status="equivalent", structural_operation="added")])]

    check_consistency(chapters, synopse)

    assert chapters[0]["interpretations"][0]["semantic_status"] == "equivalent"


# -- 11..12: axis C against the deterministic modality ---------------------------------------

def test_modal_change_called_non_normative_is_flagged():
    """A changed sentence carrying ``muss``, interpreted as non-normative text."""
    synopse = _synopse([_chapter("5<5", [
        {"kind": "changed", "confidence": 1.0, "old_ids": ["5.p1"], "new_ids": ["5.p1"],
         "old_text": "Die Prüfung ist jährlich zu wiederholen.",
         "new_text": MOVED_TEXT, "modality": {"old": "informativ", "new": "muss"}}])])
    chapters = [_deutung_chapter("5<5", [
        _interpretation(0, semantic_status="clarified",
                        normative_direction="not_applicable",
                        structural_operation="modified")])]

    report = check_consistency(chapters, synopse)

    assert chapters[0]["interpretations"][0]["axis_c_contradicts_modality"] is True
    assert report["n_axis_c_contradicts_modality"] == 1
    assert chapters[0]["interpretations"][0]["normative_direction"] == "not_applicable"


def test_informative_change_with_a_direction_is_only_counted():
    """The soft direction is counted and reported, never written on the record.

    The modality detection is known to miss a list item under a ``muss`` stem sentence,
    and a duty can be phrased without a modal verb. Marking the record would assert that
    the deterministic side is right, which is exactly what is unknown here.
    """
    synopse = _synopse([_chapter("5<5", [
        {"kind": "changed", "confidence": 1.0, "old_ids": ["5.p1"], "new_ids": ["5.p1"],
         "old_text": "Der Anhang enthält Beispiele.",
         "new_text": "Der Anhang enthält weitere Beispiele.",
         "modality": {"old": "informativ", "new": "informativ"}}])])
    chapters = [_deutung_chapter("5<5", [
        _interpretation(0, semantic_status="extended", normative_direction="tightened",
                        structural_operation="modified")])]

    report = check_consistency(chapters, synopse)

    assert "axis_c_contradicts_modality" not in chapters[0]["interpretations"][0]
    assert report["n_informative_with_direction"] == 1
    entry = next(e for e in consistency_feedback(report)
                 if e["field"] == "informative_with_direction")
    assert entry["count"] == 1


# -- 13..14: the free text against axis B ----------------------------------------------------

def test_a_named_successor_with_narrowed_is_flagged():
    """"die Regelung ist nun in Abschnitt 5.4.4 zu finden" is not a deletion.

    For training material this is the most dangerous message of all: a requirement read
    as dropped although it only moved house.
    """
    deutung = _interpretation(
        0, semantic_status="narrowed", normative_direction="relaxed",
        change="Die Anmerkung zum Resonanzfaktor wurde entfernt.",
        impact="Die Regelung ist nun in Abschnitt 5.4.4 zu finden.")

    assert successor_named(deutung) is True


def test_a_modal_verb_is_not_a_named_successor():
    """The narrow form only fires on a named place -- the wide one produces this
    false positive, which is why the narrow one is the one that runs."""
    deutung = _interpretation(
        0, semantic_status="narrowed", normative_direction="relaxed",
        change="Die Planung muss nun in enger Abstimmung mit dem Netzbetreiber erfolgen.",
        impact="")

    assert successor_named(deutung) is False


# -- 15..16: the queue, and an older artefact ------------------------------------------------

class _ByChapter:
    """Answers keyed by the tag of the request -- one answer per chapter."""

    live = True

    def __init__(self, answers: dict):
        self.answers = answers
        self.outcomes: dict[str, str] = {}

    def resolve(self, items):
        out = {}
        for tag, _prompt in items:
            answer = self.answers.get(tag)
            self.outcomes[tag] = "ok" if answer is not None else "no_answer"
            out[tag] = answer
        return out


def _docs() -> tuple[dict, dict]:
    def section(sid: str, pid: str) -> dict:
        return {"id": sid, "title": "Begriffe", "tables": [], "figures": [],
                "paragraphs": [{"id": pid, "n0": MOVED_TEXT, "n1": MOVED_TEXT}]}

    return ({"sections": [section("3.1.22", "3.1.22.p1"), section("3.1", "3.1.p1")]},
            {"sections": [section("3.1", "3.1.p91")]})


def _answer(section_id: str, **fields) -> dict:
    return {"section_id": section_id, "summary_old": "Alt.", "summary_new": "Neu.",
            "change_overview": "Verschiebung.", "training_relevance": "medium",
            "keywords": [], "practical_note": "",
            "interpretations": [{"change_index": 0, "change": "Der Absatz wurde verschoben.",
                                 "impact": "Die Regelung ist nun in Abschnitt 5.4.4 "
                                           "zu finden.",
                                 "affected_components": ["procedure"],
                                 "evidence": MOVED_TEXT, "confidence": "high", **fields}]}


def test_the_new_reasons_reach_the_review_queue(tmp_path):
    """Both new reasons show up in ``review_reasons`` of the queue, next to the old ones."""
    synopse = _synopse([
        _chapter("3.1.22<3.1", [_away("3.1.p91")]),
        _chapter("3.1<3.1", [_into("3.1.p91")]),
    ])
    old_doc, new_doc = _docs()
    answers = {
        "3.1.22_3.1": _answer("3.1.22", semantic_status="narrowed",
                              normative_direction="relaxed"),
        "3.1_3.1": _answer("3.1", semantic_status="equivalent",
                           normative_direction="unchanged"),
    }

    out = run_deutung(synopse, old_doc, new_doc, tmp_path, tmp_path / "out",
                      model="test-model", deutung_provider=_ByChapter(answers))

    reasons = {r for entry in out["review_queue"] for r in entry["review_reasons"]}
    assert "axis_partner_disagreement" in reasons
    assert "successor_named" in reasons
    assert set(REVIEW_REASONS) >= {"contradiction_flag", "evidence_ok",
                                   "change_index_disputed",
                                   "axis_partner_disagreement", "successor_named"}


def test_an_older_deutung_stays_readable():
    """A ``deutung.json`` from before the axes reports what it can, and nothing more.

    The check reads the axes off the interpretation, so a run without them produces zero
    findings instead of an exception -- the same rule the axis report follows for the
    columns of an older run.
    """
    synopse = _synopse([
        _chapter("3.1.22<3.1", [_away("3.1.p91")]),
        _chapter("3.1<3.1", [_into("3.1.p91")]),
    ])
    chapters = [
        {"section_id": "3.1.22", "mapping_id": "3.1.22<3.1",
         "deutungen": [{"change_index": 0, "semantic_label": "moved",
                        "obligation": "unchanged", "change": "Verschoben."}]},
        {"section_id": "3.1", "mapping_id": "3.1<3.1", "interpretations": []},
    ]
    before = copy.deepcopy(chapters)

    report = check_consistency(chapters, synopse)

    assert chapters == before
    assert report["n_pairs"] == 1
    assert report["n_partner_disagreement"] == 0
    assert report["n_axis_b_contradicts_a"] == 0
    assert report["n_successor_named"] == 0
