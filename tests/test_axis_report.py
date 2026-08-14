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


# -- AP-15: several runs side by side -----------------------------------------------------

#: The single-run report exactly as it stood before AP-15, captured from HEAD with
#: ``runs/AP-15_2026-08-14/skripte/golden_capture.py``. One ``--dir`` has to keep
#: producing this character for character -- the comparison view is an addition, not a
#: rewrite, and every earlier report has to stay comparable to a new one.
GOLDEN_SINGLE = r"""axis report -- out/run
normpare {version}, recomputed offline (no LLM, no network)

interpretations                      5
  carrying all four axes             4 = 80.0 %

1. semantic_status (B) x normative_direction (C)
  B \ C               tightened  not_applicable   indeterminate          (none)     sum
  -------------------------------------------------------------------------------------
  equivalent                                  1                                       1
  extended                    1                                                       1
  narrowed                    1                                                       1
  indeterminate                                               1                       1
  (none)                                                                      1       1

2. self-contradictory combinations
  four axes         0 / 5 = 0.0 %
      equivalent_but_directed                0
      not_applicable_with_component          0
      equivalent_with_proof_obligation       0
  old schema        2 / 5 = 40.0 %   (the ~7 % of the 4110 run, recomputed here)
      restricted_ambiguous                   1
      obligation_change_but_unchanged        1

3. narrowed over axis C  (the defect: restricted split 14 / 15 / 13)
      tightened                1 = 100.0 %

4. migration: semantic_label (old) x semantic_status (new)
  old \ new           equivalent        extended        narrowed   indeterminate          (none)     sum
  ------------------------------------------------------------------------------------------------------
  clarified                                                                    1                       1
  restricted                                                   1                                       1
  new_obligation                               1                                                       1
  moved                                                                                        1       1
  informative                  1                                                                       1

  structural_operation (A) of the old label 'moved':
      moved                    1

5. affected_components (D)
      proof_obligation                 1
      deadline                         1
      scope                            1
      reference                        1
      none                             1
      other:*                          1

  list length:
      0                                1
      1                                2
      2                                2

  every other: value (1), with its chapter:
      5.7.2                      3  other:Netzanschlussvertrag

6. abstention (ENT-02)
      interpretations                  1 = 20.0 %
      ambiguous_scope                  1
      of those old restricted          0
"""


def _legacy(**over) -> dict:
    """An interpretation from a run before AP-14: the flat schema and nothing else."""
    d = {"change_index": 0, "semantic_label": "restricted", "obligation": "tightened",
         "evidence": "Der Betreiber meldet die Störung binnen 24 Stunden."}
    d.update(over)
    return d


#: The interpretations of the golden run -- they have to touch all six sections.
GOLDEN_ROWS = [
    _interpretation(),
    _interpretation(change_index=1, semantic_label="new_obligation",
                    obligation="unchanged", semantic_status="extended",
                    normative_direction="tightened",
                    affected_components=["proof_obligation", "deadline"]),
    _interpretation(change_index=2, semantic_label="informative",
                    obligation="unchanged", semantic_status="equivalent",
                    normative_direction="not_applicable",
                    affected_components=["none"]),
    _interpretation(change_index=3, semantic_label="clarified", obligation="unchanged",
                    semantic_status="indeterminate", normative_direction="indeterminate",
                    indeterminate_reason="ambiguous_scope",
                    affected_components=["other:Netzanschlussvertrag", "reference"]),
    _interpretation(change_index=4, semantic_label="moved", obligation="unchanged",
                    structural_operation="moved", semantic_status=None,
                    normative_direction=None, affected_components=None),
]


def _run_report(axis_report, capsys, *dirs: Path) -> str:
    """``main`` over the given directories, in the order they are passed."""
    argv = [a for d in dirs for a in ("--dir", str(d))]
    assert axis_report.main(argv) == 0
    return capsys.readouterr().out


def _body(text: str) -> str:
    """Everything below the heading block (heading, version, digests)."""
    return text.split("\n\n", 1)[1]


def test_a_single_dir_gives_the_old_output(axis_report, tmp_path, capsys):
    """One ``--dir`` keeps the report of AP-14, character for character.

    That is a promise, not a side effect: the numbers of the AP-14 report are quoted in
    the notes and in the baseline discussion, and a comparison view that quietly
    reformats the single-run case would invalidate them.
    """
    out = _run_dir(tmp_path, GOLDEN_ROWS)
    golden = GOLDEN_SINGLE.replace("{version}", axis_report.regression._version())

    assert _report(axis_report, out) == golden

    text = _run_report(axis_report, capsys, out)
    assert text.startswith(f"axis report -- {out}\n")
    assert "sha256" in text                      # the digest line of the real call
    assert _body(text) == _body(golden) + "\n"   # print() adds the trailing newline
    assert "comparison" not in text              # no comparison view for a single run


def test_two_dirs_produce_one_column_each(axis_report, tmp_path, capsys):
    """Two runs, two columns -- in the order of the command line, not sorted."""
    alpha = _run_dir(tmp_path, [_interpretation()], name="alpha")
    beta = _run_dir(tmp_path, [_interpretation(semantic_status="clarified",
                                               normative_direction="relaxed")],
                    name="beta")

    text = _run_report(axis_report, capsys, alpha, beta)
    # both single reports are still there, and the comparison comes on top
    assert f"axis report -- {alpha}" in text and f"axis report -- {beta}" in text
    comparison = text[text.index("comparison"):]
    assert "alpha" in comparison and "beta" in comparison
    assert comparison.index("alpha") < comparison.index("beta")

    reversed_text = _run_report(axis_report, capsys, beta, alpha)
    reversed_comparison = reversed_text[reversed_text.index("comparison"):]
    assert reversed_comparison.index("beta") < reversed_comparison.index("alpha")


def test_a_run_without_the_new_axes_is_marked(axis_report, tmp_path, capsys):
    """A run from before AP-14 is carried as a column with a note, never skipped.

    Silently dropping it would make the comparison look complete when it is not; an
    invented default would be worse still. The AP-14 report printed 1749 times
    ``(none)`` for the same reason.
    """
    old = _run_dir(tmp_path, [_legacy(), _legacy(semantic_label="clarified")],
                   name="run_2026_07")
    new = _run_dir(tmp_path, [_interpretation()], name="run_2026_08")

    assert axis_report.load(old)["has_axes"] is False
    assert axis_report.load(new)["has_axes"] is True

    text = _run_report(axis_report, capsys, old, new)
    comparison = text[text.index("comparison"):]
    assert "run_2026_07" in comparison                    # carried, not skipped
    assert axis_report.NO_AXES in comparison              # and marked as empty
    assert "2" in comparison                              # its interpretations are counted


def test_other_values_of_both_runs_are_pooled(axis_report, tmp_path, capsys):
    """The ``other:`` values of every run land in one list, each with its origin."""
    a = _run_dir(tmp_path, [_interpretation(
        affected_components=["other:Netzanschlussvertrag"])], name="a4110")
    b = _run_dir(tmp_path, [_interpretation(
        affected_components=["other:Sternpunktbehandlung"])], name="b60909")

    comparison = axis_report.render_comparison([axis_report.load(a), axis_report.load(b)])
    pooled = [line for line in comparison.splitlines() if "other:" in line]
    assert any("other:Netzanschlussvertrag" in line and "a4110" in line for line in pooled)
    assert any("other:Sternpunktbehandlung" in line and "b60909" in line for line in pooled)


def test_component_counts_are_side_by_side(axis_report, tmp_path, capsys):
    """Axis D per run, absolute and in percent -- the table the two corpora differ in."""
    a = _run_dir(tmp_path, [_interpretation(affected_components=["proof_obligation"]),
                            _interpretation(affected_components=["limit_value"])],
                 name="a4110")
    b = _run_dir(tmp_path, [_interpretation(affected_components=["proof_obligation"]),
                            _interpretation(affected_components=["proof_obligation"]),
                            _interpretation(affected_components=["limit_value"])],
                 name="b60909")

    comparison = axis_report.render_comparison([axis_report.load(a), axis_report.load(b)])
    line = next(ln for ln in comparison.splitlines()
                if ln.strip().startswith("proof_obligation"))
    assert "1" in line and "50.0" in line          # 1 of 2 in the first run
    assert "2" in line and "66.67" in line         # 2 of 3 in the second


def test_comparison_is_deterministic(axis_report, tmp_path, capsys):
    """The same call twice yields character-identical output, comparison included."""
    rows = [_interpretation(affected_components=["other:Zeta", "scope"]),
            _interpretation(semantic_status="equivalent", normative_direction="tightened",
                            affected_components=["other:Alpha", "limit_value"]),
            _interpretation(semantic_status=None, normative_direction=None,
                            affected_components=None)]
    a = _run_dir(tmp_path, rows, name="a")
    b = _run_dir(tmp_path, list(reversed(rows)), name="b")

    assert _run_report(axis_report, capsys, a, b) == _run_report(axis_report, capsys, a, b)
    runs = [axis_report.load(a), axis_report.load(b)]
    assert axis_report.render_comparison(runs) == axis_report.render_comparison(runs)
