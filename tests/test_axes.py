"""The four-axis change taxonomy in the interpretation stage (AP-14, ENT-01, ENT-02).

The flat ``semantic_label`` mixes three statements. Measured on ``out/4110_2026-08b``,
1749 interpretations: ``restricted`` splits 14 / 15 / 13 over ``tightened`` /
``relaxed`` / ``unchanged``. A label cannot mean three opposite things at once -- the
cause is that "the *scope* was restricted" (a relaxation for whoever is bound by it)
and "the *requirement* was restricted" (a tightening) land in the same value.

The four axes separate them:

A ``structural_operation``
    what happens structurally. **Pipeline-owned** (ENT-51): derived from the change
    record's ``kind``, never asked of the model, discarded and counted when supplied.
B ``semantic_status``
    what happens to the *statement*, without judging its effect. ``narrowed`` is a
    statement about SCOPE, never about strictness.
C ``normative_direction``
    the direction of the duty, with ``not_applicable`` for non-normative text.
D ``affected_components``
    which components of the standard are touched, most important first, with
    ``other:<label>`` as the escape hatch that makes the vocabulary checkable.

Abstention (ENT-02) applies to B and C only: ``indeterminate`` there requires a code
from a closed vocabulary. On D it is not a value at all.

Introduction is additive -- ``semantic_label`` and ``obligation`` keep running
unchanged, so both readings are collected on the same chapters.

Synthetic text throughout, model answers supplied as dicts, no network.
"""
from __future__ import annotations

import json

from normpare.stages.deutung import (
    AFFECTED_COMPONENTS,
    INDETERMINATE_REASONS,
    NORMATIVE_DIRECTIONS,
    PIPELINE_OWNED_INTERPRETATION,
    SEMANTIC_STATUS,
    STRUCTURAL_OPERATIONS,
    FixtureProvider,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    run_deutung,
    structural_operation,
)

MODEL = "test-model"

OLD_A = "Der Betreiber meldet die Störung binnen einer Woche."
NEW_A = "Der Betreiber meldet die Störung binnen 24 Stunden."
OLD_B = "Die Anlage ist jährlich zu prüfen."
NEW_B = "Die Anlage ist halbjährlich zu prüfen."

OLD_DOC = {"sections": [{"id": "1", "title": "Betrieb", "paragraphs": [
    {"id": "1.p1", "n0": OLD_A, "n1": OLD_A},
    {"id": "1.p2", "n0": OLD_B, "n1": OLD_B}], "tables": [], "figures": []}]}
NEW_DOC = {"sections": [{"id": "1", "title": "Betrieb", "paragraphs": [
    {"id": "1.p1", "n0": NEW_A, "n1": NEW_A},
    {"id": "1.p2", "n0": NEW_B, "n1": NEW_B}], "tables": [], "figures": []}]}

#: One record per structural operation the axis has to tell apart.
CHANGES = [
    {"kind": "new", "old_text": None, "new_text": NEW_B},
    {"kind": "similar", "old_text": OLD_A, "new_text": NEW_A},
    {"kind": "removed", "old_text": OLD_B, "new_text": None},
]


def _chapter(changes: list[dict]) -> dict:
    return {"old_id": "1", "new_id": "1", "mapping_id": "1<1", "title": "Betrieb",
            "part": "hauptteil", "mode": "changed", "n_identical": 0,
            "old_ids": ["1"], "new_ids": ["1"], "tables_diff": [], "changes": changes}


def _synopse(changes: list[dict] | None = None) -> dict:
    return {"pair": "test", "chapters": [_chapter(changes if changes is not None
                                                  else list(CHANGES))]}


def _fixture_provider(path, synopse: dict, answer: dict) -> FixtureProvider:
    """A provider that returns ``answer`` for the single chapter of ``synopse``."""
    ch = synopse["chapters"][0]
    o_secs = {s["id"]: s for s in OLD_DOC["sections"]}
    n_secs = {s["id"]: s for s in NEW_DOC["sections"]}

    def excerpt(secs, ids):
        return " ".join(" ".join(p["n1"] for p in secs[sid]["paragraphs"])
                        for sid in ids if sid in secs)

    system = build_system_prompt("de", "")
    user, _sel = build_chapter_prompt(ch, excerpt(o_secs, ch["old_ids"]),
                                      excerpt(n_secs, ch["new_ids"]), o_secs, n_secs)
    path.write_text(json.dumps({cache_key(MODEL, system, user): answer},
                               ensure_ascii=False), encoding="utf-8")
    return FixtureProvider(path, model=MODEL, system=system)


def _interpretation(**over) -> dict:
    """A well-formed answer record: both the old and the new fields filled."""
    d = {"change_index": 1,
         # the old, flat schema -- kept unchanged while both run side by side
         "semantic_label": "restricted", "obligation": "tightened",
         # the new axes B, C, D (A is the pipeline's)
         "semantic_status": "narrowed", "normative_direction": "tightened",
         "affected_components": ["deadline", "proof_obligation"],
         "indeterminate_reason": "",
         "change": "Die Meldefrist wird verkürzt.", "impact": "Schnellere Meldung.",
         "cross_reference_note": "", "evidence": NEW_A, "confidence": "high",
         "contradiction_flag": False}
    d.update(over)
    return d


def _answer(**over) -> dict:
    d = {"section_id": "1", "summary_old": "Alte Fassung.", "summary_new": "Neue Fassung.",
         "change_overview": "Die Meldefrist wird verkürzt.", "training_relevance": "high",
         "keywords": [], "practical_note": "", "interpretations": [_interpretation()]}
    d.update(over)
    return d


def _run(tmp_path, answer: dict, synopse: dict | None = None) -> dict:
    syn = synopse or _synopse()
    provider = _fixture_provider(tmp_path / "answers.json", syn, answer)
    return run_deutung(syn, OLD_DOC, NEW_DOC, tmp_path, tmp_path / "out", model=MODEL,
                       deutung_provider=provider)


def _first(out: dict) -> dict:
    return out["chapters"][0]["interpretations"][0]


def _feedback(out: dict, field: str) -> list[dict]:
    """Feedback entries the pipeline wrote about a field of the answer."""
    return [f for f in out["pipeline_feedback"] if f.get("field") == field]


# -- 1: all four axes are there ----------------------------------------------------------

def test_all_four_axes_are_present(tmp_path):
    """Every interpretation carries all four axes, and the abstention code with them."""
    d = _first(_run(tmp_path, _answer()))

    assert d["structural_operation"] == "modified"      # A, from kind "similar"
    assert d["semantic_status"] == "narrowed"           # B
    assert d["normative_direction"] == "tightened"      # C
    assert d["affected_components"] == ["deadline", "proof_obligation"]   # D
    assert d["indeterminate_reason"] is None            # no abstention, no code

    # the vocabularies are closed and declared, D with its escape hatch
    assert "indeterminate" in SEMANTIC_STATUS and "narrowed" in SEMANTIC_STATUS
    assert "not_applicable" in NORMATIVE_DIRECTIONS and "indeterminate" in NORMATIVE_DIRECTIONS
    assert "proof_obligation" in AFFECTED_COMPONENTS and "none" in AFFECTED_COMPONENTS
    assert "indeterminate" not in AFFECTED_COMPONENTS
    assert set(INDETERMINATE_REASONS) == {"no_evidence", "ambiguous_scope",
                                          "conflicting_signals", "outside_text"}


# -- 2-3: axis A belongs to the pipeline -------------------------------------------------

def test_structural_operation_comes_from_the_pipeline(tmp_path):
    """Axis A is derived from the change record's ``kind``, for every kind the diff emits."""
    for kind, operation in STRUCTURAL_OPERATIONS.items():
        assert structural_operation({"kind": kind}) == operation
    # the kinds of a real run (out/4110_2026-08b) are all covered
    for kind in ("new", "removed", "similar", "cosmetic", "merged", "split",
                 "moved_in", "moved_away"):
        assert kind in STRUCTURAL_OPERATIONS
    assert structural_operation({"kind": "hausgemacht"}) is None   # fail closed
    assert structural_operation(None) is None

    # end to end: three records, three operations, each taken from its own record
    for index, operation in ((0, "added"), (1, "modified"), (2, "removed")):
        out = _run(tmp_path, _answer(interpretations=[_interpretation(change_index=index)]))
        assert _first(out)["structural_operation"] == operation, index

    # an index that addresses no record leaves the axis empty rather than guessing
    out = _run(tmp_path, _answer(interpretations=[_interpretation(change_index=99)]))
    assert _first(out)["structural_operation"] is None


def test_a_model_supplied_operation_is_discarded(tmp_path):
    """The model's own axis A never reaches the output -- and the attempt is counted."""
    assert "structural_operation" in PIPELINE_OWNED_INTERPRETATION

    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(change_index=1, structural_operation="split")]))

    assert _first(out)["structural_operation"] == "modified"    # computed, not believed
    dropped = _feedback(out, "structural_operation")
    assert len(dropped) == 1
    assert dropped[0]["count"] == 1
    assert dropped[0]["phase"] == "deutung"


# -- 4-5: the vocabularies are closed ----------------------------------------------------

def test_values_outside_the_vocabulary_are_rejected(tmp_path):
    """A value outside the vocabulary is discarded and counted, never corrected.

    Nothing here is repaired into a neighbouring value: ``restricted`` is not silently
    read as ``narrowed``, and ``verschärft`` is not read as ``tightened``. A quietly
    corrected answer would measure the correction, not the model.
    """
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        semantic_status="restricted",            # the old label, not a B value
        normative_direction="verschaerft",       # German, not the schema enum
        affected_components=["limit_value", "Nachweispflicht"])]))
    d = _first(out)

    assert d["semantic_status"] is None
    assert d["normative_direction"] is None
    assert d["affected_components"] == ["limit_value"]     # the valid entry survives

    for field in ("semantic_status", "normative_direction", "affected_components"):
        bad = _feedback(out, field)
        assert bad, field
        assert sum(f["count"] for f in bad) == 1, field
        assert all(f["phase"] == "deutung" for f in bad), field
    assert "Nachweispflicht" in _feedback(out, "affected_components")[0]["finding"]

    # a missing field is a defect of its own, counted as well
    answer = _answer(interpretations=[_interpretation()])
    for field in ("semantic_status", "normative_direction", "affected_components"):
        answer["interpretations"][0].pop(field)
    out = _run(tmp_path, answer, _synopse())
    d = _first(out)
    assert d["semantic_status"] is None
    assert d["normative_direction"] is None
    assert d["affected_components"] is None
    for field in ("semantic_status", "normative_direction", "affected_components"):
        assert _feedback(out, field), field


def test_components_keep_their_order(tmp_path):
    """Axis D is multi-valued by the ``keywords`` convention: most important first.

    The order carries the model's ranking, so it is passed through, not sorted.
    """
    supplied = ["limit_value", "proof_obligation", "deadline", "documentation"]
    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(affected_components=list(supplied))]))
    assert _first(out)["affected_components"] == supplied

    # a rejected entry drops out, the surviving ones keep their relative order
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        affected_components=["limit_value", "grenzwert", "deadline"])]))
    assert _first(out)["affected_components"] == ["limit_value", "deadline"]


def test_other_component_is_allowed_and_recorded(tmp_path):
    """``other:<label>`` survives verbatim -- it is how the vocabulary gets corrected.

    The vocabulary of axis D is a proposal, not confirmed domain knowledge. Every
    ``other:`` is kept with its free text so the report can list them all; a bare
    ``other:`` without a label carries no information and is rejected.
    """
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        affected_components=["other:Netzanschlussvertrag", "proof_obligation"])]))
    assert _first(out)["affected_components"] == ["other:Netzanschlussvertrag",
                                                  "proof_obligation"]
    assert _feedback(out, "affected_components") == []

    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(affected_components=["other:", "other:  "])]))
    assert _first(out)["affected_components"] is None
    assert _feedback(out, "affected_components")


# -- 6-7: abstention (ENT-02) on B and C, and nowhere else -------------------------------

def test_indeterminate_requires_a_reason(tmp_path):
    """An abstention without a code from the closed vocabulary is not an abstention.

    ENT-02 exists to make the hard cases visible, not to open a second way of saying
    nothing. So ``indeterminate`` without a reason is discarded and counted like any
    other invalid value.
    """
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        semantic_status="indeterminate", indeterminate_reason="ambiguous_scope")]))
    d = _first(out)
    assert d["semantic_status"] == "indeterminate"
    assert d["indeterminate_reason"] == "ambiguous_scope"
    assert _feedback(out, "semantic_status") == []

    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        semantic_status="indeterminate", indeterminate_reason="")]))
    d = _first(out)
    assert d["semantic_status"] is None
    assert d["indeterminate_reason"] is None
    assert sum(f["count"] for f in _feedback(out, "semantic_status")) == 1

    # a reason outside the closed vocabulary is no reason either -- and it is counted
    # on the reason field as well, so both defects stay visible
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        normative_direction="indeterminate", indeterminate_reason="unklar")]))
    d = _first(out)
    assert d["normative_direction"] is None
    assert d["indeterminate_reason"] is None
    assert _feedback(out, "normative_direction")
    assert _feedback(out, "indeterminate_reason")


def test_indeterminate_is_rejected_on_axis_d(tmp_path):
    """Abstention lives on B and C only. On D ``indeterminate`` is not a value."""
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        affected_components=["indeterminate"], indeterminate_reason="no_evidence")]))
    d = _first(out)

    assert d["affected_components"] is None
    assert _feedback(out, "affected_components")
    # the code justified no abstention on B or C, so it is dropped too
    assert d["indeterminate_reason"] is None


# -- 8: the additive introduction ---------------------------------------------------------

def test_old_fields_are_untouched(tmp_path):
    """``semantic_label`` and ``obligation`` come through exactly as delivered.

    Both readings are collected on the same chapters; that is the whole point of the
    additive introduction. Not even an invalid new axis may touch them.
    """
    out = _run(tmp_path, _answer(interpretations=[_interpretation(
        semantic_label="removed_obligation", obligation="unchanged",
        semantic_status="quatsch", affected_components="keine Liste")]))
    d = _first(out)

    assert d["semantic_label"] == "removed_obligation"
    assert d["obligation"] == "unchanged"
    assert d["semantic_status"] is None
    assert d["affected_components"] is None


# -- 9: the prompt asks for the new axes and not for the pipeline's -----------------------

def test_the_prompt_describes_the_new_axes(tmp_path):
    """The field descriptions carry the three axes -- and say what ``narrowed`` means.

    The confusion of scope with strictness is what produced the defect, so the
    distinction belongs into the schema documentation the model reads.
    """
    ch = _chapter(list(CHANGES))
    prompt, _sel = build_chapter_prompt(ch, OLD_A, NEW_A)

    for field in ("semantic_status", "normative_direction", "affected_components",
                  "indeterminate_reason"):
        assert field in prompt, field
    assert "structural_operation" not in prompt          # axis A is not asked for
    assert "semantic_label" in prompt and "obligation" in prompt   # the old fields stay

    described = next(line for line in prompt.splitlines() if '"semantic_status"' in line)
    assert "narrowed" in described and "scope" in described.lower()
