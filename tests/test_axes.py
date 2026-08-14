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
    CHAPTER_SCHEMA_DOC,
    INDETERMINATE_REASONS,
    NORMATIVE_DIRECTIONS,
    OTHER_COMPONENT,
    PIPELINE_OWNED_INTERPRETATION,
    SEMANTIC_STATUS,
    STRUCTURAL_OPERATIONS,
    FixtureProvider,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    check_axes,
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


def test_the_report_stage_is_unaffected(tmp_path):
    """The report keeps reading ``semantic_label``; the axes ride along unread.

    A characterization test: it is green from the start by design, because the promise
    of the additive introduction is that nothing downstream changes. Displaying the axes
    is a package of its own -- what is guarded here is that they can already be written
    into ``deutung.json`` without touching a single line of the report.
    """
    from normpare.report.synopse_final import _build_entries

    out = _run(tmp_path, _answer())
    written = json.loads((tmp_path / "out" / "deutung.json").read_text(encoding="utf-8"))
    d = written["chapters"][0]["interpretations"][0]
    assert d["structural_operation"] == "modified"      # the axes reach the file ...
    assert d["affected_components"] == ["deadline", "proof_obligation"]

    synopse = {"chapters": [{"new_id": "1", "old_id": "1", "title": "Betrieb"}]}
    entries = _build_entries(synopse, out)
    change = entries[0]["changes"][0]
    assert change["label"] == "restricted"              # ... and change nothing there
    assert change["binding"] == "tightened"
    assert set(change) == {"label", "binding", "text", "impact"}


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

    # prompt and validator have to name the same values -- a vocabulary that drifts
    # apart from its field description rejects what the model was asked for
    for vocabulary in (SEMANTIC_STATUS, NORMATIVE_DIRECTIONS, AFFECTED_COMPONENTS,
                       INDETERMINATE_REASONS):
        for value in vocabulary:
            assert value in prompt, value


# -- 10: the four separation rules of axis D (AP-15, from AP-14 F-2) ----------------------

def _component_description() -> str:
    """The one line of :data:`CHAPTER_SCHEMA_DOC` that describes axis D."""
    return next(line for line in CHAPTER_SCHEMA_DOC.splitlines()
                if '"affected_components"' in line)


def test_the_four_separation_rules_are_in_the_schema(tmp_path):
    """Axis D has to say where its values end, not only which ones exist.

    AP-14 found four pairs that are decided by the field description rather than by the
    subject matter. An undefined vocabulary is the very defect ENT-01 answers: it is what
    made ``restricted`` split 14 / 15 / 13. The rules therefore belong where the model
    reads them, in the description of the field itself.
    """
    described = _component_description().lower()

    # 1. proof_obligation against procedure -- whether/to whom against how
    assert "whether or to whom" in described
    assert "is how" in described
    assert "proof_obligation first" in described
    # 2. documentation against proof_obligation -- decided by the accepting body
    assert "records" in described
    assert "as soon as a body accepts" in described
    # 3. scope against definition -- the terms chapter or a legal definition
    assert "terms chapter" in described
    assert "legal definition" in described
    assert "scope behind it" in described
    # 4. reference against everything else -- only when it is nothing but the reference
    assert "nothing but the reference" in described
    assert "reference behind it" in described

    # and they reach the model, not just the constant
    prompt, _sel = build_chapter_prompt(_chapter(list(CHANGES)), OLD_A, NEW_A)
    for rule in ("whether or to whom", "as soon as a body accepts", "terms chapter",
                 "nothing but the reference"):
        assert rule in prompt.lower(), rule


# -- 11: the vocabulary is closed around what the first runs needed (AP-16) ---------------

#: The ten values of AP-14/AP-15, in the order they were introduced in.
OLD_COMPONENTS = ["proof_obligation", "limit_value", "procedure", "deadline",
                  "responsibility", "documentation", "scope", "definition",
                  "reference", "none"]

#: The five AP-16 adds. They are the clusters of the ``other:`` values of the first two
#: runs (428 uses, 60 % of them in these five), measured on 4110 **and** 60909.
NEW_COMPONENTS = ["formula", "note", "heading", "caption", "example"]


def test_the_five_new_components_are_accepted(tmp_path):
    """``formula``, ``note``, ``heading``, ``caption`` and ``example`` pass the validator.

    They were the five largest clusters of free ``other:`` labels of the first two runs.
    A value the model was asked for but the validator discards would be counted as a
    defect of the model, which is why prompt and vocabulary are checked together.
    """
    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(affected_components=list(NEW_COMPONENTS))]))

    assert _first(out)["affected_components"] == NEW_COMPONENTS   # order kept, none lost
    assert _feedback(out, "affected_components") == []

    # each one on its own, straight through the validator, and mixed with an old value
    for value in NEW_COMPONENTS:
        fields, bad = check_axes({"semantic_status": "clarified",
                                  "normative_direction": "unchanged",
                                  "affected_components": [value, "scope"]},
                                 {"kind": "similar"})
        assert fields["affected_components"] == [value, "scope"], value
        assert bad == [], value

    # and they are in the description the model reads, one sentence each
    described = _component_description()
    for value in NEW_COMPONENTS:
        assert value in described, value


def test_the_vocabulary_has_fifteen_values():
    """Fifteen values plus ``other:`` -- and the ten of AP-15 unchanged among them.

    ``other:`` was used in 13.9 % (4110) and 20.5 % (60909) of all interpretations, too
    much for a vocabulary meant to structure training material. The five additions close
    the recurring clusters; the remaining 30 % of genuinely subject-specific single cases
    are what ``other:`` stays there for, so the escape hatch is not touched.
    """
    assert AFFECTED_COMPONENTS == ["proof_obligation", "limit_value", "procedure",
                                   "deadline", "responsibility", "documentation",
                                   "scope", "definition", "reference",
                                   "formula", "note", "heading", "caption", "example",
                                   "none"]
    assert len(AFFECTED_COMPONENTS) == 15
    assert len(set(AFFECTED_COMPONENTS)) == 15
    assert OTHER_COMPONENT == "other:"

    # the ten older values survive, in their old relative order -- every earlier run
    # stays comparable value by value
    assert [c for c in AFFECTED_COMPONENTS if c in OLD_COMPONENTS] == OLD_COMPONENTS
    assert set(NEW_COMPONENTS) == set(AFFECTED_COMPONENTS) - set(OLD_COMPONENTS)
    assert "indeterminate" not in AFFECTED_COMPONENTS     # abstention lives on B and C

    described = _component_description()
    assert "|".join(AFFECTED_COMPONENTS) in described     # still one pipe-separated list
    assert "other:<short label>" in described
    assert "most important first" in described


def test_the_fifth_separation_rule_is_in_the_schema():
    """Terminology and notation stay with ``definition``; ``formula`` is the equation.

    They deliberately did **not** become a value of their own: a sixth value overlapping
    ``definition`` would build in the next ambiguity, which is the defect ``restricted``
    stands for. A separation rule costs a sentence and no vocabulary.
    """
    described = _component_description().lower()

    assert "definition also for a changed designation" in described
    assert "spelling" in described and "symbol notation" in described
    assert "formula only when the equation itself changes" in described
    assert "not its name" in described

    # the four rules of AP-15 are untouched by the fifth
    for rule in ("whether or to whom", "as soon as a body accepts", "terms chapter",
                 "nothing but the reference"):
        assert rule in described, rule

    prompt, _sel = build_chapter_prompt(_chapter(list(CHANGES)), OLD_A, NEW_A)
    assert "formula only when the equation itself changes" in prompt.lower()


def test_artefacts_are_directed_to_pipeline_feedback():
    """A torn formula is a finding about the preprocessing, not a component of a standard.

    ``other:Formelfragment``, ``other:Textfragment`` and their relatives made up 4.7 % of
    the free values of the first two runs. The field for them exists (``pipeline_feedback``,
    asked for in the same answer), so the description says where they belong instead of
    the vocabulary growing a value for a defect of our own pipeline.
    """
    described = _component_description()
    lowered = described.lower()

    assert "pipeline_feedback" in described
    assert "preprocessing artefact" in lowered
    assert "not on this axis" in lowered

    prompt, _sel = build_chapter_prompt(_chapter(list(CHANGES)), OLD_A, NEW_A)
    assert "not on this axis" in prompt.lower()
    assert "pipeline_feedback" in prompt
