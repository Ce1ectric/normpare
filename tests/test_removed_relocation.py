"""AP-10: where the removed text actually stands (``tools/removed_relocation.py``).

AP-09 showed that ``old_surplus`` predicts ``removed`` reports without measuring itself.
It did not show *which* stage produces them, and there are exactly two candidates that
ask for opposite repairs:

* the text stands in a section **outside** the ``new_ids`` of its mapping -- then the
  chapter mapping paired wrongly and the paragraph aligner never saw the counterpart;
* the text stands in a section **inside** ``new_ids`` -- then the mapping was right and
  the pairing failed.

The tool separates the two mechanically, from a finished run, without interpreting a
single sentence of standard text. These tests build their own miniature runs; no test
reads ``out/``, none needs real standard text and none touches the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "removed_relocation",
    Path(__file__).resolve().parents[1] / "tools" / "removed_relocation.py")
removed_relocation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(removed_relocation)


#: A full sentence, comfortably above the 60-character floor of the tool.
DUTY = ("Der Betreiber bewahrt das Prüfbuch nach Anlage 3 zehn Jahre lang auf und legt "
        "es der Netzbetreiberin auf Verlangen vor.")

#: Same length, no shared wording -- the filler that must never produce a hit.
FILLER = ("Die Bemessungsspannung der Kundenanlage richtet sich nach dem Anschlusspunkt "
          "und wird im Netzanschlussvertrag festgelegt.")


# -- miniature runs -----------------------------------------------------------------

def _para(pid: str, text: str, kind: str = "text") -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": kind}


def _doc(doc_id: str, sections: list[tuple[str, str, list[str]]]) -> dict:
    """``norm_doc.json`` from ``(section id, title, [paragraph texts])`` triples."""
    return {"doc_id": doc_id, "source": {"format": "docx", "sha256": "0" * 8},
            "sections": [{"id": sid, "title": title, "level": 1, "part": "hauptteil",
                          "paragraphs": [_para(f"{sid}.p{i}", t)
                                         for i, t in enumerate(texts)],
                          "tables": [], "figures": [], "formulas": []}
                         for sid, title, texts in sections]}


def _chapter(mapping_id: str, old_ids: list[str], new_ids: list[str],
             removed: list[str], title: str = "Kapitel") -> dict:
    return {"mapping_id": mapping_id,
            "old_id": old_ids[0] if old_ids else None,
            "new_id": new_ids[0] if new_ids else None,
            "old_ids": old_ids, "new_ids": new_ids, "title": title,
            "mode": "id+title", "map_confidence": 1.0,
            "changes": [{"kind": "removed", "confidence": 0.0,
                         "old_ids": [f"{old_ids[0]}.p{i}"], "new_ids": [],
                         "old_text": text, "new_text": None,
                         "modality": {"old": "muss"}}
                        for i, text in enumerate(removed)]}


def _synopse(chapters: list[dict]) -> dict:
    return {"pair": "alt <-> neu", "old_doc": {"doc_id": "alt"},
            "new_doc": {"doc_id": "neu"}, "stats": {}, "chapters": chapters}


def _rows(chapters: list[dict], old_doc: dict, new_doc: dict) -> list[dict]:
    return removed_relocation.evaluate(_synopse(chapters), new_doc, old_doc)["rows"]


def _one_row(*, where: str, new_ids: list[str], text: str = DUTY) -> dict:
    """One removal in mapping ``3 < A``; ``where`` is the new section holding ``text``."""
    old_doc = _doc("alt", [("A", "Kapitel alt", [text, FILLER])])
    new_doc = _doc("neu", [("3", "Zugeordnetes Kapitel", [FILLER]),
                           ("9", "Fremdes Kapitel", [FILLER])])
    for sec in new_doc["sections"]:
        if sec["id"] == where:
            sec["paragraphs"].append(_para(f"{where}.p9", text))
    rows = _rows([_chapter("3<A", ["A"], new_ids, [text])], old_doc, new_doc)
    assert len(rows) == 1, rows
    return rows[0]


def _write_run(tmp_path: Path, old_doc: dict, new_doc: dict, synopse: dict) -> Path:
    run = tmp_path / "run"
    (run / "alt").mkdir(parents=True)
    (run / "neu").mkdir(parents=True)
    for path, data in ((run / "alt" / "norm_doc.json", old_doc),
                       (run / "neu" / "norm_doc.json", new_doc),
                       (run / "synopse.json", synopse)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return run


def _demo_run(tmp_path: Path) -> Path:
    """A run holding all three verdicts at once."""
    inside = ("Die Übergabestation wird nach den anerkannten Regeln der Technik "
              "errichtet und jährlich durch eine Fachkraft begutachtet.")
    gone = ("Die Anlage nach Nummer 7 ist vor der ersten Inbetriebnahme vollständig "
            "zu prüfen und schriftlich abzunehmen.")
    old_doc = _doc("alt", [("A", "Kapitel alt", [DUTY, inside, gone])])
    new_doc = _doc("neu", [("3", "Zugeordnetes Kapitel", [FILLER, inside]),
                           ("9", "Fremdes Kapitel", [FILLER, DUTY])])
    syn = _synopse([_chapter("3<A", ["A"], ["3"], [DUTY, inside, gone])])
    return _write_run(tmp_path, old_doc, new_doc, syn)


def _snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(directory)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(directory.rglob("*")) if p.is_file()}


# -- the classification ---------------------------------------------------------------

def test_text_found_outside_the_mapping_blames_the_mapping():
    row = _one_row(where="9", new_ids=["3"])
    assert row["best_section"] == "9"
    assert row["in_mapping"] is False
    assert row["best_score"] == 1.0
    assert removed_relocation.verdict(row, 0.7) == "relocated_outside_mapping"


def test_text_found_inside_the_mapping_blames_the_aligner():
    row = _one_row(where="3", new_ids=["3"])
    assert row["best_section"] == "3"
    assert row["in_mapping"] is True
    assert row["best_score"] == 1.0
    assert removed_relocation.verdict(row, 0.7) == "missed_inside_mapping"


def test_absent_text_is_not_found():
    old_doc = _doc("alt", [("A", "Kapitel alt", [DUTY])])
    new_doc = _doc("neu", [("3", "Zugeordnetes Kapitel", [FILLER]),
                           ("9", "Fremdes Kapitel", [FILLER])])
    rows = _rows([_chapter("3<A", ["A"], ["3"], [DUTY])], old_doc, new_doc)
    assert rows[0]["best_score"] < 0.5
    assert removed_relocation.verdict(rows[0], 0.5) == "not_found"


def test_trigram_overlap_is_order_sensitive():
    # the trap this replaces: a word *set* comparison scores these two texts as
    # identical, which produced a three-digit false finding in the pre-study
    shuffled = " ".join(reversed(DUTY.rstrip(".").split())) + "."
    from normpare.text.textnorm import n3
    assert set(n3(DUTY).split()) == set(n3(shuffled).split())

    old_doc = _doc("alt", [("A", "Kapitel alt", [DUTY])])
    new_doc = _doc("neu", [("3", "Zugeordnetes Kapitel", [shuffled])])
    rows = _rows([_chapter("3<A", ["A"], ["3"], [DUTY])], old_doc, new_doc)
    assert rows[0]["best_score"] < 0.5
    assert removed_relocation.verdict(rows[0], 0.5) == "not_found"


def test_short_removals_are_skipped():
    short = "Die Anlage ist zu prüfen."
    assert len(short) < removed_relocation.MIN_CHARS <= len(DUTY)
    old_doc = _doc("alt", [("A", "Kapitel alt", [short, DUTY])])
    new_doc = _doc("neu", [("3", "Zugeordnetes Kapitel", [short, DUTY])])
    result = removed_relocation.evaluate(
        _synopse([_chapter("3<A", ["A"], ["3"], [short, DUTY])]), new_doc, old_doc)
    assert result["skipped_short"] == 1
    assert result["total_removed"] == 2
    assert [r["old_text"] for r in result["rows"]] == [DUTY]


def test_threshold_sweep_is_monotone():
    rows = [{"best_score": s, "in_mapping": inside, "old_surplus": 0}
            for s, inside in ((0.95, False), (0.85, True), (0.75, False), (0.65, True),
                              (0.55, False), (0.45, True), (0.0, False))]
    sweep = removed_relocation.sweep(rows, removed_relocation.THRESHOLDS)
    assert [t for t in sweep] == sorted(removed_relocation.THRESHOLDS)

    not_found = [sweep[t]["not_found"] for t in sweep]
    found = [sweep[t]["relocated_outside_mapping"] + sweep[t]["missed_inside_mapping"]
             for t in sweep]
    assert not_found == sorted(not_found)
    assert found == sorted(found, reverse=True)
    assert all(n + f == len(rows) for n, f in zip(not_found, found))


# -- the tool over a whole run -----------------------------------------------------

def test_the_tool_writes_nothing_to_the_run(tmp_path):
    run = _demo_run(tmp_path)
    before = _snapshot(run)
    assert removed_relocation.main(
        ["--dir", str(run), "--out", str(tmp_path / "bericht"), "--name", "demo"]) == 0
    assert _snapshot(run) == before


def test_report_is_deterministic(tmp_path):
    run = _demo_run(tmp_path)
    first, second = tmp_path / "a", tmp_path / "b"
    for dest in (first, second):
        removed_relocation.main(
            ["--dir", str(run), "--out", str(dest), "--name", "demo"])
    for name in ("relocation_demo.txt", "faelle_demo.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    # and the case list really carries the relocated case, and only that one
    faelle = (first / "faelle_demo.md").read_text(encoding="utf-8")
    assert "gefunden in `9`" in faelle
    assert faelle.count("\n## ") == 1
