"""The axis report (``tools/axis_report.py``, AP-14).

The tool answers the one question the four axes were introduced for: is the share of
self-contradictory interpretations down from the roughly 7 % the flat schema produced,
and does ``narrowed`` stop spreading evenly over the normative direction the way
``restricted`` did?

It reads a finished ``deutung.json`` and nothing else -- no LLM, no network, no
interpretation of standard text. Every test builds its own miniature run; none reads
``out/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def axis_report(load_tool):
    return load_tool("axis_report")


def _interpretation(**over) -> dict:
    d = {"change_index": 0, "structural_operation": "modified",
         "semantic_label": "restricted", "obligation": "tightened",
         "semantic_status": "narrowed", "normative_direction": "tightened",
         "affected_components": ["scope"], "indeterminate_reason": None,
         "evidence": "Der Betreiber meldet die Störung binnen 24 Stunden."}
    d.update(over)
    return d


def _run_dir(tmp_path: Path, interpretations: list[dict], name: str = "run") -> Path:
    """A miniature output directory with a single chapter of interpretations."""
    out = tmp_path / name
    out.mkdir(parents=True, exist_ok=True)
    deutung = {"model": "test-model", "mode": "api", "language": "de",
               "n_chapters": 1, "n_llm": 1,
               "chapters": [{"section_id": "5.7.2", "mapping_id": "5.7.2<5.7.2",
                             "_source": "llm", "interpretations": interpretations}],
               "review_queue": [], "pipeline_feedback": []}
    (out / "deutung.json").write_text(json.dumps(deutung, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    return out


def _report(axis_report, out_dir: Path, label: str = "out/run") -> str:
    """The rendered report; ``label`` stands in for the directory name in the heading."""
    rows = axis_report.collect(out_dir)
    return axis_report.render(rows, axis_report.summarize(rows), label, {})


# -- the contradiction rules --------------------------------------------------------------

def test_contradiction_rules_catch_the_known_cases(axis_report, tmp_path):
    """The three rules of AP-14, part 3, each on its own case -- and a clean row.

    The rules are a constant of the module, not a shape of the output: the share they
    produce is the success measure of the whole package, so it has to be readable in
    one place.
    """
    cases = {
        # equivalent statement, yet the duty is said to change
        "equivalent_but_directed": _interpretation(semantic_status="equivalent",
                                                   normative_direction="tightened"),
        # non-normative text that nevertheless touches a normative component
        "not_applicable_with_component": _interpretation(
            semantic_status="clarified", normative_direction="not_applicable",
            affected_components=["proof_obligation"]),
        # the statement is unchanged, yet a proof obligation is affected
        "equivalent_with_proof_obligation": _interpretation(
            semantic_status="equivalent", normative_direction="unchanged",
            affected_components=["proof_obligation"]),
    }
    names = [name for name, _ in axis_report.CONTRADICTIONS]
    assert names == list(cases)

    for name, row in cases.items():
        assert axis_report.contradictions(row) == [name], name

    clean = _interpretation(semantic_status="narrowed", normative_direction="relaxed",
                            affected_components=["scope"])
    assert axis_report.contradictions(clean) == []
    # a relaxed non-normative row with 'none' as its only component is fine as well
    assert axis_report.contradictions(_interpretation(
        semantic_status="equivalent", normative_direction="not_applicable",
        affected_components=["none"])) == []

    out = _run_dir(tmp_path, list(cases.values()) + [clean])
    rows = axis_report.collect(out)
    summary = axis_report.summarize(rows)
    assert summary["contradictory"] == 3
    assert summary["contradictory_pct"] == 75.0
    assert summary["interpretations"] == 4


def test_the_legacy_share_is_measured_on_the_same_run(axis_report, tmp_path):
    """The 7 % of the old schema is recomputed, not quoted.

    Both shares come from the same interpretations, so the comparison holds even where
    the corpus is a different one. ``restricted`` counts as contradictory in itself: it
    splits 14 / 15 / 13 over the three directions and cannot mean all three.
    """
    names = [name for name, _ in axis_report.LEGACY_CONTRADICTIONS]
    assert names == ["restricted_ambiguous", "obligation_change_but_unchanged"]

    rows = axis_report.collect(_run_dir(tmp_path, [
        _interpretation(semantic_label="restricted", obligation="relaxed"),
        _interpretation(semantic_label="new_obligation", obligation="unchanged"),
        _interpretation(semantic_label="clarified", obligation="unchanged"),
        _interpretation(semantic_label="equivalent", obligation="unchanged"),
    ]))
    summary = axis_report.summarize(rows)
    assert summary["legacy_contradictory"] == 2
    assert summary["legacy_contradictory_pct"] == 50.0


# -- what the report has to show ----------------------------------------------------------

def test_other_values_are_listed_in_full(axis_report, tmp_path):
    """Every ``other:`` value appears with its chapter -- that is how the vocabulary
    of axis D gets corrected after the run."""
    out = _run_dir(tmp_path, [
        _interpretation(affected_components=["other:Netzanschlussvertrag", "scope"]),
        _interpretation(affected_components=["limit_value"]),
    ])
    rows = axis_report.collect(out)
    summary = axis_report.summarize(rows)

    assert summary["other_components"] == [{"component": "other:Netzanschlussvertrag",
                                            "section_id": "5.7.2", "change_index": 0}]
    text = _report(axis_report, out)
    assert "other:Netzanschlussvertrag" in text
    assert "5.7.2" in text


def test_the_report_shows_both_schemas(axis_report, tmp_path):
    """Cross table, ``narrowed`` distribution, migration table and abstention.

    The cross table has the shape of the finding it answers (axis B against axis C);
    the migration table shows how each old label splits over the new axis, which is
    what the later replacement of the old field will be decided on.
    """
    out = _run_dir(tmp_path, [
        _interpretation(semantic_label="restricted", obligation="tightened",
                        semantic_status="narrowed", normative_direction="tightened"),
        _interpretation(semantic_label="restricted", obligation="relaxed",
                        semantic_status="narrowed", normative_direction="relaxed",
                        affected_components=["scope", "other:Anschlussvertrag"]),
        _interpretation(semantic_label="informative", obligation="unchanged",
                        semantic_status="equivalent", normative_direction="not_applicable",
                        affected_components=["none"]),
        _interpretation(semantic_label="clarified", obligation="unchanged",
                        semantic_status="indeterminate", normative_direction="indeterminate",
                        indeterminate_reason="ambiguous_scope"),
    ])
    rows = axis_report.collect(out)
    summary = axis_report.summarize(rows)

    assert summary["cross"][("narrowed", "tightened")] == 1
    assert summary["cross"][("equivalent", "not_applicable")] == 1
    assert summary["narrowed"] == {"tightened": 1, "relaxed": 1}
    assert summary["migration"][("restricted", "narrowed")] == 2
    assert summary["migration"][("informative", "equivalent")] == 1
    assert summary["components"]["scope"] == 3
    assert summary["component_list_lengths"] == {1: 3, 2: 1}
    assert summary["indeterminate_reasons"] == {"ambiguous_scope": 1}
    # the abstention above sits on an interpretation the old schema called 'clarified',
    # not 'restricted' -- the overlap with the old defect is reported, not assumed
    assert summary["indeterminate_on_restricted"] == 0

    text = _report(axis_report, out)
    for expected in ("semantic_status", "normative_direction", "narrowed",
                     "semantic_label", "ambiguous_scope", "not_applicable"):
        assert expected in text, expected


def test_axis_report_is_deterministic(axis_report, tmp_path):
    """The same run twice yields character-identical output.

    Byte reproducibility is a core property of the pipeline (ENT-25); a report that
    orders its rows by a set would quietly break it.
    """
    interpretations = [
        _interpretation(semantic_status="narrowed", normative_direction="relaxed",
                        affected_components=["scope", "other:Zeta"]),
        _interpretation(semantic_status="equivalent", normative_direction="tightened",
                        affected_components=["other:Alpha", "limit_value"]),
        _interpretation(semantic_status="clarified", normative_direction="indeterminate",
                        indeterminate_reason="conflicting_signals",
                        affected_components=["definition"]),
        _interpretation(semantic_status=None, normative_direction=None,
                        affected_components=None),
    ]
    first = _report(axis_report, _run_dir(tmp_path, interpretations, name="a"))
    second = _report(axis_report, _run_dir(tmp_path, interpretations, name="b"))
    assert first == second

    # and twice over the very same directory
    out = _run_dir(tmp_path, interpretations, name="c")
    assert _report(axis_report, out) == _report(axis_report, out)


def test_a_run_without_deutung_is_refused(axis_report, tmp_path):
    """A missing ``deutung.json`` is a stated error, not an empty report."""
    with pytest.raises(SystemExit):
        axis_report.collect(tmp_path / "nothing")
