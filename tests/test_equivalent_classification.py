"""AP-38: ``equivalent`` without a counterpart -- classify first, then decide.

The AP-28 rule says a change without a counterpart cannot be ``equivalent``: there is no
earlier statement it could be equal to. The rule does not work and the share rose again
(116 / 72 / 63 = 4,7 % / 3,1 % / 3,4 % over the three runs of 2026-09-01). AP-37, 6.4
showed it is **not** an information problem -- not one of the 251 records carries a
``possible_move_to``, and the rule needs nothing but axis A, which is in the prompt.

Sharpening the rule blindly would hit four different things at once. The free texts read
like at least four matters:

* a **relocation** with a recognisable counterpart ("wird aus dem alten Absatz [2]
  hierher verschoben") -- there ``replaced`` would be right, and the model is wrong;
* a **torn continuation**, an extraction artefact ("ist aber Teil der fortgesetzten
  Definition") -- there the *change record* is wrong, not the interpretation;
* a **duplicate** of another change record of the same chapter -- likewise;
* a **genuine addition** without any counterpart ("Quellenangabe wird ergänzt") -- there
  and only there ``extended`` is simply the right value.

For the two middle groups ``equivalent`` is the most honest answer the vocabulary offers:
the pipeline claims a change that is none, and the model says so in the same breath, in
``pipeline_feedback``. Forcing them to ``extended`` would be worse than today.

So this package measures before it changes anything: ``tools/equivalent_without_counterpart.py``
assigns every record to one of the four groups mechanically, counts the remainder that
fits none of them, and the consistency check counts a record whose change is reported in
the chapter's ``pipeline_feedback`` **apart** instead of dropping it.

No test here touches the network, none reads ``out/``, none interprets standard text: the
group of a record follows from signals that are on disk.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from normpare.stages.deutung import (
    NORMATIVE_DIRECTIONS,
    SEMANTIC_STATUS,
    WITHOUT_COUNTERPART,
    check_axes,
    check_consistency,
    consistency_feedback,
)

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "equivalent_without_counterpart",
    ROOT / "tools" / "equivalent_without_counterpart.py")
ewc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ewc)


# -- material ------------------------------------------------------------------------------

SENTENCE = ("Die Quellenangabe für den Begriff wird um eine internationale Quelle "
            "ergänzt und damit belegt.")


def _change(kind: str = "new", text: str = SENTENCE, **extra) -> dict:
    """A change record without a counterpart -- ``new`` carries only the new text."""
    rec = {"kind": kind, "confidence": 1.0,
           "old_ids": [] if kind == "new" else ["5.p1"],
           "new_ids": ["5.p1"] if kind == "new" else [],
           "old_text": None if kind == "new" else text,
           "new_text": text if kind == "new" else None,
           "modality": {"new" if kind == "new" else "old": "informativ"}}
    rec.update(extra)
    return rec


def _chapter(mapping_id: str, changes: list[dict]) -> dict:
    old, new = (mapping_id.split("<") + [mapping_id])[:2]
    return {"old_id": old, "new_id": new, "mapping_id": mapping_id, "title": "Begriffe",
            "part": "hauptteil", "mode": "changed", "n_identical": 0,
            "old_ids": [old], "new_ids": [new], "tables_diff": [], "changes": changes}


def _synopse(chapters: list[dict]) -> dict:
    return {"pair": "alt <-> neu", "chapters": chapters}


def _interpretation(index: int, change_text: str, **extra) -> dict:
    """One interpretation as ``deutung.json`` holds it after the axes were checked."""
    return {"change_index": index, "semantic_status": "equivalent",
            "normative_direction": "unchanged", "affected_components": ["scope"],
            "structural_operation": "added", "change": change_text, "impact": "",
            "evidence": SENTENCE, "confidence": "high", "evidence_ok": True, **extra}


def _deutung_chapter(mapping_id: str, interpretations: list[dict],
                     feedback: list[dict] | None = None) -> dict:
    return {"section_id": mapping_id.split("<")[-1], "mapping_id": mapping_id,
            "summary_old": "", "summary_new": "", "change_overview": "",
            "pipeline_feedback": feedback or [],
            "interpretations": interpretations, "_source": "llm"}


def _feedback(finding: str, indices: list[int], phase: str = "diff") -> dict:
    return {"phase": phase, "finding": finding, "change_indices": indices}


def _group(chapters: list[dict], synopse: dict, index: int = 0) -> str:
    """The group the classifier gives the record ``index`` of the first chapter."""
    rows = ewc.rows(chapters, synopse)
    return next(r["group"] for r in rows if r["change_index"] == index)


def _run_dir(tmp_path: Path, chapters: list[dict], synopse: dict,
             name: str = "run") -> Path:
    """A finished run as the tool reads it: the two artefacts and nothing else."""
    dest = tmp_path / name
    dest.mkdir(parents=True)
    (dest / "synopse.json").write_text(json.dumps(synopse, ensure_ascii=False),
                                       encoding="utf-8")
    (dest / "deutung.json").write_text(
        json.dumps({"pair": "alt <-> neu", "chapters": chapters}, ensure_ascii=False),
        encoding="utf-8")
    return dest


# -- 1..4: the four groups and the remainder -------------------------------------------------

def test_the_classifier_finds_a_feedback_backed_case():
    """The change is reported as an artefact in ``pipeline_feedback`` -- the torn group.

    The strongest trace and the one already measured: the model says at the place
    provided for it that the change record itself is wrong. Alone it does not decide --
    the free text has to speak of an artefact as well.
    """
    synopse = _synopse([_chapter("3.5<3.6", [_change(text="Netznennspannung")])])
    chapters = [_deutung_chapter("3.5<3.6", [_interpretation(
        0, "Das Wort erscheint als neues Fragment, ist aber Teil der fortgesetzten "
           "Definition und inhaltlich unverändert.")],
        [_feedback("Die Änderungsliste enthält mehrere Fragmente, die der "
                   "Diff-Algorithmus aus einem Satz gebildet hat.", [0])])]

    row = ewc.rows(chapters, synopse)[0]

    assert row["group"] == "torn"
    assert "feedback" in row["signals"] and "voice_artefact" in row["signals"]


def test_the_classifier_finds_a_named_predecessor():
    """The free text names a place -- the relocation group, where ``replaced`` is right."""
    synopse = _synopse([_chapter("4.2.3<4.2.3", [_change()])])
    chapters = [_deutung_chapter("4.2.3<4.2.3", [_interpretation(
        0, "Der Text wird aus dem alten Absatz [2] hierher verschoben; der Verweis auf "
           "Abschnitt B.12 bleibt bestehen.")])]

    row = ewc.rows(chapters, synopse)[0]

    assert row["group"] == "relocation"
    assert "voice_move" in row["signals"] and "names_place" in row["signals"]


def test_the_classifier_finds_a_duplicate():
    """The free text names another change of the same chapter -- the duplicate group."""
    synopse = _synopse([_chapter("5.5<5.5", [_change(), _change()])])
    chapters = [_deutung_chapter("5.5<5.5", [_interpretation(
        1, "Dies ist ein Duplikat der Änderung [0] ohne das Wort 'Abschnitt'; der "
           "Inhalt ist identisch.")],
        [_feedback("Die Änderung [1] ist ein Duplikat der Änderung [0].", [1])])]

    row = ewc.rows(chapters, synopse)[0]

    assert row["group"] == "duplicate"
    assert "voice_duplicate" in row["signals"] and "names_change" in row["signals"]
    # a number named by the free text only counts as a partner if the chapter has it
    assert row["named_changes"] == [0]


def test_an_unclassifiable_case_is_counted_as_such():
    """A classification that houses everything has bent itself, so the remainder counts.

    A fragment whose free text speaks of no artefact, no move and no duplicate, and
    which nothing reports: it is neither the genuine addition (that is a whole sentence)
    nor any of the three others.
    """
    synopse = _synopse([_chapter("6.8.1<6.8.1", [_change(text="Dabei ist")])])
    chapters = [_deutung_chapter("6.8.1<6.8.1", [_interpretation(
        0, "Einleitung der Formelzeichen-Erläuterung.")])]

    run = ewc.classify_run(chapters, synopse)

    assert _group(chapters, synopse) == "unclassified"
    assert run["counts"]["unclassified"] == 1
    assert set(run["counts"]) == set(ewc.GROUPS)
    assert sum(run["counts"].values()) == run["n_flagged"] == 1


def test_a_genuine_addition_is_told_from_the_artefacts():
    """The one group where ``equivalent`` is simply the wrong value: a whole new sentence."""
    synopse = _synopse([_chapter("3.1<3.1", [_change()])])
    chapters = [_deutung_chapter("3.1<3.1", [_interpretation(
        0, "Die Quellenangabe wird ergänzt: IEV 161-08-13:2015-05.")])]

    assert _group(chapters, synopse) == "genuine"


# -- 5..6: the tool ---------------------------------------------------------------------------

def test_the_classifier_takes_several_runs(tmp_path):
    """Several ``--dir``, one comparison column per run, in the order of the command line.

    The rule from CLAUDE.md: a finding on one corpus is a property of that corpus.
    """
    synopse = _synopse([_chapter("3.1<3.1", [_change()])])
    chapters = [_deutung_chapter("3.1<3.1", [_interpretation(
        0, "Die Quellenangabe wird ergänzt: IEV 161-08-13:2015-05.")])]
    a = _run_dir(tmp_path, chapters, synopse, "run_a")
    b = _run_dir(tmp_path, chapters, synopse, "run_b")
    out = tmp_path / "bericht"

    assert ewc.main(["--dir", str(a), "--dir", str(b), "--out", str(out)]) == 0

    comparison = (out / "equivalent_without_counterpart_comparison.txt").read_text(
        encoding="utf-8")
    assert comparison.index("run_a") < comparison.index("run_b")
    for group in ewc.GROUPS:
        assert group in comparison
    assert (out / "equivalent_without_counterpart_run_a.txt").exists()
    assert (out / "equivalent_without_counterpart_run_b.txt").exists()
    # deterministic: the same input written twice gives the same bytes
    again = tmp_path / "bericht2"
    ewc.main(["--dir", str(a), "--dir", str(b), "--out", str(again)])
    assert (again / "equivalent_without_counterpart_comparison.txt").read_bytes() == \
        comparison.encode("utf-8")


def test_the_tool_writes_only_to_out(tmp_path):
    """``guard_write`` bites: the reference runs below ``out/`` are irreplaceable."""
    synopse = _synopse([_chapter("3.1<3.1", [_change()])])
    chapters = [_deutung_chapter("3.1<3.1", [_interpretation(0, "Ergänzt.")])]
    run = _run_dir(tmp_path, chapters, synopse)
    forbidden = tmp_path / "out" / "4110_hot"

    with pytest.raises(ewc.regression.WriteToOutError):
        ewc.main(["--dir", str(run), "--out", str(forbidden)])

    assert not forbidden.exists()


# -- 7..9: part B -- the exception, counted apart ---------------------------------------------

def test_a_feedback_backed_case_is_counted_apart():
    """Reported in ``pipeline_feedback``: own counter, and the flag is not deleted.

    The flag claims the interpretation contradicts the kind of change -- but the kind of
    change is exactly what is being disputed here, at the place provided for it. So it
    does not count towards ``axis_b_contradicts_a``; it counts, visibly, next to it.
    """
    synopse = _synopse([_chapter("3.5<3.6", [_change(text="Netznennspannung")])])
    chapters = [_deutung_chapter("3.5<3.6", [_interpretation(0, "Teil der Definition.")],
                                 [_feedback("Fragment aus einem Satz.", [0])])]

    report = check_consistency(chapters, synopse)

    assert report["n_axis_b_contradicts_a"] == 0
    assert report["n_axis_b_reported_as_artefact"] == 1
    # marked, never corrected: both flags stand on the record and the value is untouched
    d = chapters[0]["interpretations"][0]
    assert d["axis_b_contradicts_a"] is True
    assert d["axis_b_reported_as_artefact"] is True
    assert d["semantic_status"] == "equivalent"


def test_a_case_without_feedback_still_counts():
    """The exception only bites where the chapter reports *this* change.

    A report about another change of the same chapter is not a report about this one,
    and a chapter without any feedback counts exactly as before.
    """
    synopse = _synopse([_chapter("3.1<3.1", [_change(), _change()])])
    chapters = [_deutung_chapter(
        "3.1<3.1", [_interpretation(0, "Die Quellenangabe wird ergänzt."),
                    _interpretation(1, "Die Quellenangabe wird ergänzt.")],
        [_feedback("Fragment.", [1])])]

    report = check_consistency(chapters, synopse)

    assert report["n_axis_b_contradicts_a"] == 1
    assert report["n_axis_b_reported_as_artefact"] == 1
    first, second = chapters[0]["interpretations"]
    assert first["axis_b_contradicts_a"] is True
    assert "axis_b_reported_as_artefact" not in first
    assert second["axis_b_reported_as_artefact"] is True


def test_the_feedback_names_both_counts():
    """Both numbers, at zero as well -- a number that only shows when it is bad is unmeasured."""
    synopse = _synopse([_chapter("3.1<3.1", [_change()])])
    chapters = [_deutung_chapter("3.1<3.1", [_interpretation(
        0, "Ergänzt.", semantic_status="extended")])]

    report = check_consistency(chapters, synopse)
    entry = next(e for e in consistency_feedback(report)
                 if e["field"] == "axis_b_contradicts_a")

    assert report["n_axis_b_contradicts_a"] == report["n_axis_b_reported_as_artefact"] == 0
    assert entry["count"] == 0
    assert entry["n_reported_as_artefact"] == 0
    assert "0" in entry["finding"] and "pipeline_feedback" in entry["finding"]


# -- 10..11: what stays untouched -------------------------------------------------------------

def test_check_axes_is_untouched():
    """The vocabulary check of AP-14 is not the place of this package.

    ``equivalent`` stays an admissible value of axis B on every change; what AP-38 moves
    is the consistency flag of AP-29, not the vocabulary.
    """
    for operation in WITHOUT_COUNTERPART:
        kind = "new" if operation == "added" else "removed"
        fields, bad = check_axes({"semantic_status": "equivalent",
                                  "normative_direction": "unchanged",
                                  "affected_components": ["scope"]}, {"kind": kind})
        assert fields["semantic_status"] == "equivalent"
        assert fields["structural_operation"] == operation
        assert bad == []


def test_the_vocabulary_is_unchanged():
    """No new value on axis B, and no new axis field: an exception, not a vocabulary."""
    assert SEMANTIC_STATUS == ["equivalent", "clarified", "extended", "narrowed",
                               "replaced", "contradictory", "indeterminate"]
    assert NORMATIVE_DIRECTIONS == ["tightened", "relaxed", "unchanged",
                                    "not_applicable", "indeterminate"]
    fields, _bad = check_axes({"semantic_status": "equivalent",
                               "normative_direction": "unchanged",
                               "affected_components": ["scope"]}, {"kind": "new"})
    assert set(fields) == {"structural_operation", "semantic_status",
                           "normative_direction", "affected_components",
                           "indeterminate_reason"}
