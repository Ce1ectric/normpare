"""Field ownership and the cross-check of ``change_index`` (AP-07, ENT-51).

AP-06 found that the five duplicate chapter ids were never in ``synopse.json``: they
came out of the model answers. The defect was not an ambiguous key, it was a key the
pipeline believed. Hence the rule:

    Every field the pipeline knows itself is set by the pipeline.

Two things follow, and both are tested here:

*class P*
    a pipeline-owned field supplied by the model is **discarded and counted**, never
    silently overwritten -- the count is what shows how often the model invents
    something it was never asked for.
*class V*
    ``change_index`` is a genuine choice, so it is not overwritten either. It is
    cross-checked: the quote is scored against *every* change record of the chapter,
    and a strictly better candidate marks the interpretation ``disputed`` -- flag, do
    not decide (same construction as ``evidence_unique``, ENT-30).

Synthetic text throughout, no standard, no network.
"""
from __future__ import annotations

import json

from normpare.stages.deutung import (
    PIPELINE_OWNED_ASSET,
    PIPELINE_OWNED_CHAPTER,
    PIPELINE_OWNED_INTERPRETATION,
    FixtureProvider,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    change_index_check,
    check_evidence,
    drop_pipeline_owned,
    run_deutung,
)

MODEL = "test-model"

# The two paragraphs open with the same eleven words and end differently: a quote of the
# second one still passes the historical 15-character probe against the first record, so
# ``evidence_ok`` and ``change_index_disputed`` can be observed independently.
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


def _chapter(changes: list[dict]) -> dict:
    return {"old_id": "1", "new_id": "1", "mapping_id": "1<1", "title": "Betrieb",
            "part": "hauptteil", "mode": "changed", "n_identical": 0,
            "old_ids": ["1"], "new_ids": ["1"], "tables_diff": [], "changes": changes}


#: Record 0 and record 1 share their first eleven words; record 2 shares nothing.
CHANGES = [
    {"kind": "changed", "old_text": OLD_A, "new_text": OLD_A},
    {"kind": "changed", "old_text": OLD_A, "new_text": NEW_A},
    {"kind": "changed", "old_text": OLD_B, "new_text": NEW_B},
]


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
    d = {"change_index": 1, "semantic_label": "restricted", "obligation": "tightened",
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


# -- 1-2: class P -- the pipeline owns its own fields ------------------------------------

def test_pipeline_owned_field_from_model_is_dropped(tmp_path):
    """A pipeline-owned value from the answer never reaches the output.

    Checked twice: on the helper (every declared field disappears, whatever it held)
    and end to end, where the model claims a ``change_index_best`` of its own -- a field
    the pipeline computes and the model was never asked for.
    """
    supplied = {f: "vom Modell" for f in PIPELINE_OWNED_CHAPTER}
    supplied["summary_new"] = "bleibt"
    dropped = drop_pipeline_owned(supplied, PIPELINE_OWNED_CHAPTER)
    assert dropped == {f: "vom Modell" for f in PIPELINE_OWNED_CHAPTER}
    assert supplied == {"summary_new": "bleibt"}

    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(change_index_best=99, change_index_disputed=True,
                        evidence_unique=True)]))
    d = out["chapters"][0]["interpretations"][0]
    assert d["change_index_best"] == 1        # computed, not the model's 99
    assert d["change_index_disputed"] is False
    assert d["evidence_unique"] is True       # computed as well, not believed

    # and the declarations cover the three levels of the answer
    assert "section_id" in PIPELINE_OWNED_CHAPTER
    assert "evidence_ok" in PIPELINE_OWNED_INTERPRETATION
    assert "evidence_ok" in PIPELINE_OWNED_ASSET


def test_dropped_field_is_counted(tmp_path):
    """Discarding is reported: the count goes into ``pipeline_feedback``.

    Five invented chapter ids made 165 interpretations uncheckable in the 4110 run and
    nothing in the output said so. A silent overwrite would repeat that.
    """
    out = _run(tmp_path, _answer(section_id="anhang_informativ"))

    dropped = [f for f in out["pipeline_feedback"] if f.get("field") == "section_id"]
    assert len(dropped) == 1
    assert dropped[0]["count"] == 1
    assert dropped[0]["phase"] == "deutung"
    assert dropped[0]["section_id"] == "1"          # the chapter, as the pipeline knows it
    assert "anhang_informativ" in dropped[0]["finding"]
    assert out["chapters"][0]["section_id"] == "1"


# -- 3-6, 8: class V -- change_index is checked, not overwritten -------------------------

def test_change_index_best_finds_the_stronger_record(tmp_path):
    """A quote that fits record 1 better than the chosen record 0 points at record 1."""
    res = change_index_check(_interpretation(change_index=0, evidence=NEW_A),
                            _chapter(list(CHANGES)))
    assert res["change_index_best"] == 1
    assert res["change_index_disputed"] is True


def test_change_index_not_disputed_when_choice_is_best(tmp_path):
    """The chosen record is the best match -- nothing to dispute."""
    res = change_index_check(_interpretation(change_index=1, evidence=NEW_A),
                            _chapter(list(CHANGES)))
    assert res["change_index_best"] == 1
    assert res["change_index_disputed"] is False


def test_change_index_not_disputed_on_a_tie(tmp_path):
    """Two records match the quote equally well: a tie is no contradiction.

    ``change_index_best`` still names one of them (the lowest index, deterministically),
    but the model's choice is not called into question.
    """
    ch = _chapter([{"kind": "changed", "old_text": OLD_A, "new_text": NEW_A},
                   {"kind": "changed", "old_text": OLD_A, "new_text": NEW_A}])
    res = change_index_check(_interpretation(change_index=1, evidence=NEW_A), ch)
    assert res["change_index_best"] == 0
    assert res["change_index_disputed"] is False


def test_disputed_never_overwrites_the_choice(tmp_path):
    """The model's choice survives the cross-check untouched.

    It may be right for a reason the text similarity cannot see -- it may interpret a
    connection instead of quoting it. Correcting automatically would replace a right
    choice with a more similar one.
    """
    d = _interpretation(change_index=0, evidence=NEW_A)
    d.update(change_index_check(d, _chapter(list(CHANGES))))
    assert d["change_index_disputed"] is True
    assert d["change_index"] == 0

    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(change_index=0, evidence=NEW_A)]))
    stored = out["chapters"][0]["interpretations"][0]
    assert stored["change_index"] == 0
    assert stored["change_index_disputed"] is True


def test_disputed_implies_not_unique(tmp_path):
    """``disputed`` true forces ``evidence_unique`` false -- by construction, not by luck.

    A quote that is strict in the chosen record covers all of its characters, so no
    other record can score strictly higher. If both were ever true at once, one of the
    two definitions would be wrong.
    """
    ch = _chapter(list(CHANGES))
    for index in range(len(CHANGES)):
        for quote in (NEW_A, OLD_A, NEW_B, "Der Betreiber meldet die Störung", ""):
            d = _interpretation(change_index=index, evidence=quote)
            d.update(check_evidence(d, ch))
            d.update(change_index_check(d, ch))
            if d["change_index_disputed"]:
                assert d["evidence_unique"] is False, (index, quote)


# -- 7: the review queue is extended, not rebuilt ----------------------------------------

def test_disputed_enters_the_review_queue(tmp_path):
    """A disputed interpretation reaches the queue with a reason of its own.

    The quote passes the historical 15-character probe against the chosen record, so
    ``evidence_ok`` is true and the entry would not be in the queue at all today. Its
    reason has to name the cross-check, not the evidence guard.
    """
    out = _run(tmp_path, _answer(interpretations=[
        _interpretation(change_index=0, evidence=NEW_A)]))

    assert len(out["review_queue"]) == 1
    entry = out["review_queue"][0]
    assert entry["evidence_ok"] is True
    assert entry["review_reasons"] == ["change_index_disputed"]
    assert entry["change_index"] == 0
    assert entry["mapping_id"] == "1<1"

    # an interpretation that is neither disputed nor unsupported stays out of the queue
    clean = _run(tmp_path, _answer())
    assert clean["review_queue"] == []
