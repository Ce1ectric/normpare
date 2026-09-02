"""The counterpart of a move in the prompt, and the rule for axis C (AP-37).

AP-29 gave axis B a rule for moves ("unchanged moved text is equivalent, text reworded on
the way takes the value of the rewording") and it worked: axis B disagreement between the
two records of one move fell from 66 / 88 / 54 % to 1,4 / 1,3 / 3,9 %. Axis C got no such
rule and is now the largest item of the review queue -- 24 / 33 / 7 disagreeing pairs, and
the whole growth of the queue from 17 / 25 / 13 to 63 / 104 / 31 entries.

The two sides almost never argue about the *direction*; they argue about whether the text
is normative at all. And they cannot know: ``_change_block`` writes ``ALT:`` only when the
change record carries ``old_text`` and ``NEU:`` only for ``new_text``. A ``moved_away``
record carries only the old text, a ``moved_in`` record only the new one -- of the
counterpart the prompt held nothing but a pointer. The axis B rule was therefore not
decidable either: the model cannot see whether the text was reworded on the way.

So two things, and the second only works because of the first:

* the move shows its counterpart, resolved over ``moved_to`` / ``moved_from``, labelled in
  the direction it belongs to (the counterpart of ``moved_away`` is the **new** text) and
  clipped like every other text in the block;
* axis C gets its rule: a move changes nothing about the duty, the same value on both
  sides.

Synthetic text throughout, no network, no fixture touched.
"""
from __future__ import annotations

import copy

from normpare.stages.deutung import (
    CHAPTER_SCHEMA_DOC,
    NORMATIVE_DIRECTIONS,
    SEMANTIC_STATUS,
    _change_block,
    _paramap,
    build_chapter_prompt,
    check_axes,
    check_consistency,
)

OLD_SIDE = "Der Flickerkoeffizient ist nach Anhang C zu bestimmen."
NEW_SIDE = "Der Flickerkoeffizient ist nach Anhang D zu bestimmen."

AWAY_ID = "3.1.22.p1"          # where the paragraph stood in the old edition
INTO_ID = "3.1.p91"            # where it stands in the new one


# -- material ------------------------------------------------------------------------------

def _away(moved_to: str | None = INTO_ID, text: str = OLD_SIDE, **extra) -> dict:
    rec = {"kind": "moved_away", "confidence": 1.0, "old_ids": [AWAY_ID], "new_ids": [],
           "old_text": text, "new_text": None, "modality": {"old": "informativ"},
           "moved_to": moved_to}
    rec.update(extra)
    return rec


def _into(moved_from: str | None = AWAY_ID, text: str = NEW_SIDE, **extra) -> dict:
    rec = {"kind": "moved_in", "confidence": 1.0, "old_ids": [], "new_ids": [INTO_ID],
           "old_text": None, "new_text": text, "modality": {"new": "informativ"},
           "moved_from": moved_from}
    rec.update(extra)
    return rec


def _secs(para_id: str, text: str) -> dict:
    """A section index of one section holding one paragraph -- what ``_paramap`` reads."""
    section = para_id.rsplit(".p", 1)[0]
    return {section: {"id": section, "title": "Begriffe", "tables": [], "figures": [],
                      "paragraphs": [{"id": para_id, "n0": text, "n1": text}]}}


def _chapter(mapping_id: str, changes: list[dict], **extra) -> dict:
    old, new = (mapping_id.split("<") + [mapping_id])[:2]
    return {"old_id": old, "new_id": new, "mapping_id": mapping_id,
            "title": "Begriffe", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": [old], "new_ids": [new],
            "tables_diff": [], "changes": changes, **extra}


def _prompt(ch: dict, o_secs: dict, n_secs: dict) -> str:
    prompt, _sel = build_chapter_prompt(ch, "", "", o_secs, n_secs)
    return prompt


# -- 1..2: the counterpart, resolved and labelled -------------------------------------------

def test_a_moved_away_block_shows_the_new_text():
    """``moved_to`` resolves into the new document, and the text is labelled NEU.

    The direction is the whole point: a wrong label would be worse than none. What the old
    chapter lost stands under ALT, where it went stands under NEU -- exactly as for any
    paired change, which is what a move is.
    """
    n_paras = _paramap(_secs(INTO_ID, NEW_SIDE))

    block = _change_block(0, _away(), {}, n_paras)

    assert f"    ALT: {OLD_SIDE}" in block
    assert f"    NEU: {NEW_SIDE}" in block
    assert block.index("ALT:") < block.index("NEU:")
    assert _prompt(_chapter("3.1.22<3.1", [_away()]), {}, _secs(INTO_ID, NEW_SIDE)) \
        .count(f"NEU: {NEW_SIDE}") == 1


def test_a_moved_in_block_shows_the_old_text():
    """``moved_from`` resolves into the old document, and the text is labelled ALT."""
    o_paras = _paramap(_secs(AWAY_ID, OLD_SIDE))

    block = _change_block(0, _into(), o_paras, {})

    assert f"    ALT: {OLD_SIDE}" in block
    assert f"    NEU: {NEW_SIDE}" in block
    assert block.index("ALT:") < block.index("NEU:")
    assert _prompt(_chapter("3.1<3.1", [_into()]), _secs(AWAY_ID, OLD_SIDE), {}) \
        .count(f"ALT: {OLD_SIDE}") == 1


# -- 3..4: where there is nothing to resolve ------------------------------------------------

def test_a_block_continuation_shows_no_counterpart():
    """AP-26: a block continuation proves the chapter, not the paragraph.

    There is no ``moved_to``, so there is no counterpart to show, and the chapter is not
    one -- the line stays what it was.
    """
    rec = _away(moved_to=None, moved_to_chapter="3.1<3.1", via="block", confidence=0.0)

    block = _change_block(0, rec, {}, _paramap(_secs(INTO_ID, NEW_SIDE)))

    assert "    (verschoben nach Kapitel 3.1<3.1)" in block
    assert "NEU:" not in block
    assert block == _change_block(0, rec)


def test_an_unresolvable_pointer_shows_no_counterpart():
    """A pointer into a paragraph the document does not have: today's behaviour, no
    placeholder and no guess."""
    n_paras = _paramap(_secs("9.4.p7", NEW_SIDE))

    block = _change_block(0, _away(), {}, n_paras)

    assert "NEU:" not in block
    assert block == _change_block(0, _away())
    assert f"    (verschoben nach {INTO_ID})" in block


# -- 5: the same cap as every other text ----------------------------------------------------

def test_the_counterpart_is_clipped():
    """900 characters and an ellipsis -- the cap of the change texts, not a second one."""
    long_text = "Der Nachweis ist zu führen. " * 60          # 1680 characters
    n_paras = _paramap(_secs(INTO_ID, long_text))

    block = _change_block(0, _away(), {}, n_paras)

    assert f"    NEU: {long_text[:900]} …" in block
    assert long_text not in block


# -- 6..7: what must not move ---------------------------------------------------------------

def test_a_paired_change_is_unchanged():
    """An ordinary change renders character for character as it always has."""
    rec = {"kind": "changed", "confidence": 0.91,
           "old_ids": ["11.2.p3"], "new_ids": ["11.2.p3"],
           "old_text": "Die Anlage ist jährlich zu prüfen.",
           "new_text": "Die Anlage ist halbjährlich zu prüfen.",
           "modality": {"old": "muss", "new": "muss"},
           "kennwerte": {"changed": [], "added": [], "removed": []}}

    block = _change_block(4, rec, _paramap(_secs(AWAY_ID, OLD_SIDE)),
                          _paramap(_secs(INTO_ID, NEW_SIDE)))

    assert block == ("[4] TYP=changed\n"
                     "    Modalität: muss → muss\n"
                     "    ALT: Die Anlage ist jährlich zu prüfen.\n"
                     "    NEU: Die Anlage ist halbjährlich zu prüfen.")


def test_the_pointer_line_survives():
    """The counterpart is added, the pointer is not replaced by it."""
    away = _change_block(0, _away(), {}, _paramap(_secs(INTO_ID, NEW_SIDE)))
    into = _change_block(0, _into(), _paramap(_secs(AWAY_ID, OLD_SIDE)), {})

    assert f"    (verschoben nach {INTO_ID})" in away
    assert f"    (hierher verschoben aus {AWAY_ID})" in into


# -- 8..10: the rules in the schema ---------------------------------------------------------

def test_the_schema_states_the_rule_for_axis_c():
    """The rule is only followable because of part A: "the same value on both sides"
    presupposes that both sides see the same thing."""
    rule = ("A move changes nothing about the duty: the axis describes what the RELOCATION "
            "means for whoever is bound, and that is nothing. If the moved text carries a "
            "duty, answer unchanged; if it is non-normative, answer not_applicable -- the "
            "same value on BOTH sides. tightened or relaxed only if the text was changed "
            "on the way and the change itself shifts the duty.")

    assert rule in CHAPTER_SCHEMA_DOC
    assert rule in _prompt(_chapter("3.1<3.1", [_into()]), _secs(AWAY_ID, OLD_SIDE), {})


def test_the_vocabulary_is_unchanged():
    """A rule, not a value: no new entry on axis C and no new axis field."""
    assert SEMANTIC_STATUS == ["equivalent", "clarified", "extended", "narrowed",
                               "replaced", "contradictory", "indeterminate"]
    assert NORMATIVE_DIRECTIONS == ["tightened", "relaxed", "unchanged", "not_applicable",
                                    "indeterminate"]
    fields, _bad = check_axes({"semantic_status": "equivalent",
                               "normative_direction": "unchanged",
                               "affected_components": ["procedure"]},
                              {"kind": "moved_away"})
    assert set(fields) == {"structural_operation", "semantic_status",
                           "normative_direction", "affected_components",
                           "indeterminate_reason"}


def test_axis_b_rule_is_untouched():
    """The AP-29 rule stands verbatim -- part A makes it answerable, it does not change it."""
    assert ("A moved change is the same statement in a new place: the axis describes the "
            "TEXT, never the place (the place is structural). Unchanged moved text is "
            "equivalent; text reworded on the way takes the value that describes the "
            "rewording (clarified|extended|narrowed). Keep replaced for a change whose "
            "counterpart is not connected by a move.") in CHAPTER_SCHEMA_DOC


# -- 11: the guard stays ---------------------------------------------------------------------

def test_the_partner_check_still_flags_a_disagreement():
    """``axis_partner_disagreement`` is the success measure of the rule and unchanged.

    If the number does not fall on the next run, the rule did not get through -- exactly
    as AP-29 arranged it for axis B.
    """
    synopse = {"pair": "alt <-> neu", "chapters": [
        _chapter("3.1.22<3.1", [_away()]), _chapter("3.1<3.1", [_into()])]}

    def _deutung(mapping_id: str, direction: str) -> dict:
        return {"section_id": mapping_id.split("<")[-1], "mapping_id": mapping_id,
                "summary_old": "", "summary_new": "", "change_overview": "",
                "interpretations": [{"change_index": 0, "semantic_status": "equivalent",
                                     "normative_direction": direction,
                                     "affected_components": ["definition"],
                                     "structural_operation": "moved",
                                     "change": "Der Absatz wurde verschoben.",
                                     "impact": "", "evidence": OLD_SIDE,
                                     "confidence": "high", "evidence_ok": True}],
                "_source": "llm"}

    chapters = [_deutung("3.1.22<3.1", "unchanged"),
                _deutung("3.1<3.1", "not_applicable")]
    agreeing = [_deutung("3.1.22<3.1", "unchanged"), _deutung("3.1<3.1", "unchanged")]
    before = copy.deepcopy(agreeing)

    report = check_consistency(chapters, synopse)

    assert report["n_pairs"] == 1 and report["n_partner_disagreement"] == 1
    assert report["by_axis"]["normative_direction"] == 1
    assert chapters[0]["interpretations"][0]["axis_partner_disagreement"] == [
        {"axis": "normative_direction", "value": "unchanged",
         "partner_value": "not_applicable",
         "partner_mapping_id": "3.1<3.1", "partner_change_index": 0}]
    assert check_consistency(agreeing, synopse)["n_partner_disagreement"] == 0
    assert agreeing == before
