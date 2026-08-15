"""AP-17 -- the four axes become visible: change CSV, component view, axis marker.

The axes have existed in ``deutung.json`` since AP-14 and appear in no output document.
These tests fix what the three new views promise: a spreadsheet-ready CSV with a stable
column order, a markdown view grouped by affected component rather than by chapter, and
a short marker behind the existing label in the final synopsis and in the HTML.

Everything is built from dicts. No run under ``out/`` is read, no LLM is called and no
value is interpreted -- the axis values in the fixtures are inputs, not judgements.
"""
from __future__ import annotations

import csv

from normpare.report.axes import axis_marker, carries_axes, change_rows
from normpare.report.changes_csv import CSV_COLUMNS, DELIMITER, ENCODING, build_changes_csv
from normpare.report.component_view import build_component_view, render_component_view
from normpare.stages.deutung import AFFECTED_COMPONENTS

DATE = "2026-08-15"


def _synopse() -> dict:
    """Two chapters, three change records -- the deterministic side of a run."""
    return {
        "pair": "TR-X 1000 alt <-> neu",
        "old_doc": "alt", "new_doc": "neu",
        "chapters": [
            {"mapping_id": "5<5", "old_id": "5", "new_id": "5", "new_ids": ["5"],
             "title": "Anschlussbedingungen", "mode": "id+title",
             "changes": [{"kind": "similar", "new_ids": ["5.p1"], "old_ids": ["5.p1"]},
                         {"kind": "new", "new_ids": ["5.p2"], "old_ids": []}]},
            {"mapping_id": "9<9", "old_id": "9", "new_id": "9", "new_ids": ["9"],
             "title": "Nachweise", "mode": "id+title",
             "changes": [{"kind": "removed", "new_ids": [], "old_ids": ["9.p1"]}]},
        ],
    }


def _deutung() -> dict:
    """Three interpretations, all four axes filled -- one of them multi-valued."""
    return {
        "language": "de", "model": "test",
        "chapters": [
            {"section_id": "5", "mapping_id": "5<5",
             "change_overview": "Zwei Änderungen.", "training_relevance": "high",
             "interpretations": [
                 {"change_index": 0,
                  "semantic_label": "restricted", "obligation": "relaxed",
                  "structural_operation": "modified", "semantic_status": "narrowed",
                  "normative_direction": "relaxed",
                  "affected_components": ["scope", "limit_value"],
                  "indeterminate_reason": None,
                  "change": "Der Anwendungsbereich wurde eingeschränkt.",
                  "impact": "Weniger Anlagen betroffen.",
                  "evidence": "gilt nur für Anlagen über 135 kW",
                  "evidence_ok": True, "confidence": "high"},
                 {"change_index": 1,
                  "semantic_label": "new_obligation", "obligation": "tightened",
                  "structural_operation": "added", "semantic_status": "extended",
                  "normative_direction": "tightened",
                  "affected_components": ["proof_obligation"],
                  "indeterminate_reason": None,
                  "change": "Ein Nachweis wurde eingeführt; er ist vorzulegen.",
                  "impact": "Neue Pflicht.",
                  "evidence": "Der Nachweis ist vorzulegen", "evidence_ok": True,
                  "confidence": "medium"},
             ]},
            {"section_id": "9", "mapping_id": "9<9",
             "interpretations": [
                 {"change_index": 0,
                  "semantic_label": "equivalent", "obligation": "unchanged",
                  "structural_operation": "removed", "semantic_status": "indeterminate",
                  "normative_direction": "indeterminate",
                  "affected_components": ["other:Sternpunktbehandlung", "note"],
                  "indeterminate_reason": "ambiguous_scope",
                  "change": "Eine Anmerkung entfiel.",
                  "impact": "", "evidence": "", "evidence_ok": False,
                  "confidence": "low"},
             ]},
        ],
    }


def _deutung_without_axes() -> dict:
    """The same run as it came out of a pre-AP-14 pipeline: no axis field at all."""
    deutung = _deutung()
    for ch in deutung["chapters"]:
        for i in ch["interpretations"]:
            for axis in ("structural_operation", "semantic_status", "normative_direction",
                         "affected_components", "indeterminate_reason"):
                i.pop(axis, None)
    return deutung


def _read(path) -> str:
    return path.read_text(encoding="utf-8-sig")


# -- Teil 1: the change CSV --------------------------------------------------------------

def test_csv_has_all_columns_in_order(tmp_path):
    """The header is exactly the agreed column list, in the agreed order."""
    out = build_changes_csv(_synopse(), _deutung(), tmp_path / "Aenderungen.csv")
    header = _read(out).splitlines()[0]
    assert header.split(DELIMITER) == list(CSV_COLUMNS)
    assert list(CSV_COLUMNS) == [
        "section_id", "mapping_id", "chapter_title", "change_index", "change_kind",
        "structural_operation", "semantic_status", "normative_direction",
        "affected_components", "indeterminate_reason", "semantic_label", "obligation",
        "change", "impact", "evidence", "evidence_ok", "confidence"]
    # one line per interpretation, plus the header
    assert len(_read(out).splitlines()) == 4


def test_components_are_pipe_separated_in_order(tmp_path):
    """Axis D is one column; the delivered order ("most important first") survives."""
    out = build_changes_csv(_synopse(), _deutung(), tmp_path / "Aenderungen.csv")
    rows = [line.split(DELIMITER) for line in _read(out).splitlines()]
    col = list(CSV_COLUMNS).index("affected_components")
    assert rows[1][col] == "scope|limit_value"          # not "limit_value|scope"
    assert rows[2][col] == "proof_obligation"
    assert rows[3][col] == "other:Sternpunktbehandlung|note"


def test_csv_is_excel_readable(tmp_path):
    """UTF-8 with BOM and semicolons -- the German Excel convention, on purpose."""
    out = build_changes_csv(_synopse(), _deutung(), tmp_path / "Aenderungen.csv")
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.split(b"\n")[0].count(b";") == len(CSV_COLUMNS) - 1
    assert "eingeschränkt" in _read(out)              # the BOM does not break the umlauts
    # a semicolon inside a field must not become a column boundary
    deutung = _deutung()
    deutung["chapters"][0]["interpretations"][0]["change"] = "Eins; zwei; drei."
    out2 = build_changes_csv(_synopse(), deutung, tmp_path / "b.csv")
    text = _read(out2)
    assert '"Eins; zwei; drei."' in text                  # quoted, so it stays one cell
    with open(out2, newline="", encoding=ENCODING) as fh:
        parsed = list(csv.reader(fh, delimiter=DELIMITER))
    assert all(len(row) == len(CSV_COLUMNS) for row in parsed)
    assert parsed[1][list(CSV_COLUMNS).index("change")] == "Eins; zwei; drei."


def test_csv_survives_a_run_without_axes(tmp_path):
    """A pre-AP-14 run: empty cells for the five axis columns, no KeyError, no default."""
    out = build_changes_csv(_synopse(), _deutung_without_axes(), tmp_path / "alt.csv")
    rows = [line.split(DELIMITER) for line in _read(out).splitlines()]
    idx = {name: i for i, name in enumerate(CSV_COLUMNS)}
    assert len(rows) == 4
    for row in rows[1:]:
        for axis in ("structural_operation", "semantic_status", "normative_direction",
                     "affected_components", "indeterminate_reason"):
            assert row[idx[axis]] == ""
        assert row[idx["semantic_label"]]            # the old fields are still there
        assert row[idx["obligation"]]
    assert rows[1][idx["chapter_title"]] == "Anschlussbedingungen"
    assert rows[1][idx["change_kind"]] == "similar"


# -- Teil 2: the component view ----------------------------------------------------------

def test_component_view_has_one_section_per_component(tmp_path):
    """All fifteen vocabulary values get a section -- an unused one too, or "0" and
    "never offered" would look the same."""
    text = render_component_view(change_rows(_synopse(), _deutung()), "TR-X", DATE)
    for component in AFFECTED_COMPONENTS:
        assert f"## `{component}`" in text
    assert len(AFFECTED_COMPONENTS) == 15
    assert "_(keine)_" in text                      # the empty ones say so


def test_a_change_with_two_components_appears_twice(tmp_path):
    """Axis D is multi-valued; an interpretation belongs under every component it names."""
    text = render_component_view(change_rows(_synopse(), _deutung()), "TR-X", DATE)
    assert text.count("Der Anwendungsbereich wurde eingeschränkt.") == 2   # scope + limit_value
    assert text.count("Ein Nachweis wurde eingeführt; er ist vorzulegen.") == 1
    scope = text.index("## `scope`")
    limit = text.index("## `limit_value`")
    for start in (scope, limit):
        assert "Der Anwendungsbereich wurde eingeschränkt." in text[start:start + 1200]


def test_component_order_is_the_vocabulary_order(tmp_path):
    """Sections follow AFFECTED_COMPONENTS, not frequency -- so the view stays
    comparable between runs."""
    text = render_component_view(change_rows(_synopse(), _deutung()), "TR-X", DATE)
    positions = [text.index(f"## `{c}`") for c in AFFECTED_COMPONENTS]
    assert positions == sorted(positions)
    # frequency would put proof_obligation (1) behind scope (1) only by chance: check the
    # rarer value really precedes the more frequent one where the vocabulary says so
    assert text.index("## `proof_obligation`") < text.index("## `scope`")


def test_other_values_get_their_own_section(tmp_path):
    """The escape hatch is reported with its free labels, after the closed vocabulary."""
    text = render_component_view(change_rows(_synopse(), _deutung()), "TR-X", DATE)
    assert "## `other:`" in text
    assert "other:Sternpunktbehandlung" in text
    assert text.index("## `other:`") > text.index(f"## `{AFFECTED_COMPONENTS[-1]}`")
    assert "ohne Komponentenangabe" in text


def test_component_view_notes_a_run_without_axes(tmp_path):
    """The view is written for an old run too, and says why it is empty."""
    rows = change_rows(_synopse(), _deutung_without_axes())
    text = render_component_view(rows, "TR-X", DATE, carries=False)
    assert "führt die vier Achsen nicht" in text
    assert "## `scope`" in text
    assert not carries_axes(_deutung_without_axes())
    assert carries_axes(_deutung())


# -- Teil 3: the marker in the existing outputs -------------------------------------------

def test_the_marker_shows_the_three_model_axes(tmp_path):
    """B, C and D as one short marker -- in the final synopsis and in the HTML."""
    from normpare.report.html import build_annotated_html
    from normpare.report.synopse_final import _build_entries

    assert axis_marker(_deutung()["chapters"][0]["interpretations"][0]) == \
        "[narrowed · relaxed · scope, limit_value]"

    entries = _build_entries(_synopse(), _deutung())
    markers = [c["marker"] for e in entries for c in e["changes"]]
    assert "[narrowed · relaxed · scope, limit_value]" in markers
    assert "[extended · tightened · proof_obligation]" in markers

    out = tmp_path / "a.html"
    build_annotated_html(_new_doc(), _synopse(), _deutung(), out, "TR-X")
    html = out.read_text(encoding="utf-8")
    assert "[extended · tightened · proof_obligation]" in html
    assert 'title="ambiguous_scope"' in html          # the abstention reason as a tooltip


def test_the_marker_is_absent_without_axes(tmp_path):
    """No axes, no marker -- and no empty brackets standing in for one."""
    from normpare.report.html import build_annotated_html
    from normpare.report.synopse_final import _build_entries

    deutung = _deutung_without_axes()
    assert axis_marker(deutung["chapters"][0]["interpretations"][0]) == ""
    entries = _build_entries(_synopse(), deutung)
    assert all(c["marker"] == "" for e in entries for c in e["changes"])

    out = tmp_path / "alt.html"
    build_annotated_html(_new_doc(), _synopse(), deutung, out, "TR-X")
    html = out.read_text(encoding="utf-8")
    assert 'class="axes"' not in html
    # no placeholder either: the label (with its obligation badge) runs straight into the text
    assert "</span> — Ein Nachweis wurde eingeführt" in html


def _new_doc() -> dict:
    """The minimal new version the HTML report needs."""
    return {
        "title": "TR-X 1000 (neu)",
        "sections": [
            {"id": "5", "title": "Anschlussbedingungen", "level": 1, "part": "hauptteil",
             "paragraphs": [{"id": "5.p1", "kind": "text", "n1": "Alter Satz."},
                            {"id": "5.p2", "kind": "text", "n1": "Der Nachweis ist vorzulegen."}],
             "tables": [], "figures": [], "keywords": []},
            {"id": "9", "title": "Nachweise", "level": 1, "part": "hauptteil",
             "paragraphs": [{"id": "9.p9", "kind": "text", "n1": "Bleibt."}],
             "tables": [], "figures": [], "keywords": []},
        ],
    }


# -- determinism --------------------------------------------------------------------------

def test_deliverables_are_deterministic(tmp_path):
    """Same input twice -> byte-identical CSV and markdown."""
    syn, deut = _synopse(), _deutung()
    first = build_changes_csv(syn, deut, tmp_path / "1" / "a.csv").read_bytes()
    second = build_changes_csv(syn, deut, tmp_path / "2" / "a.csv").read_bytes()
    assert first == second

    md1 = build_component_view(syn, deut, tmp_path / "1" / "k.md", "TR-X", date=DATE)
    md2 = build_component_view(syn, deut, tmp_path / "2" / "k.md", "TR-X", date=DATE)
    assert md1.read_bytes() == md2.read_bytes()
    assert axis_marker(deut["chapters"][0]["interpretations"][0]) == \
        axis_marker(deut["chapters"][0]["interpretations"][0])
