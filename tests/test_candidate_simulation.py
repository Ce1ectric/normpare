"""AP-12: what a wider candidate circle would buy (``tools/candidate_simulation.py``).

AP-10 measured 32 removals across three corpora whose text still stands in the new
edition, in a section the paragraph aligner never got to see: it only ever pairs an old
paragraph with new paragraphs from the ``new_ids`` of *its own* record. AP-11 confirmed
the shape and found 14 of them literally identical.

Changing the mapper is expensive -- every baseline breaks and every deviation has to be
judged one by one. The benefit was never measured. This tool measures it, on a finished
run, without touching ``align/``: four candidate rules, and for each of them how many of
the relocated cases would come into reach, how much the candidate circle grows, and how
far the rule dissolves the record boundaries.

Nothing is decided here and no rule is implemented. These tests build their own miniature
runs; no test reads ``out/``, none needs real standard text and none touches the network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "candidate_simulation", ROOT / "tools" / "candidate_simulation.py")
candidate_simulation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(candidate_simulation)


#: A full sentence, comfortably above the 60-character floor of the relocation measure.
DUTY = ("Der Betreiber bewahrt das Prüfbuch nach Anlage 3 zehn Jahre lang auf und legt "
        "es der Netzbetreiberin auf Verlangen vor.")

#: A second one. Two cases in one document need two texts -- the same sentence twice
#: would let the trigram search find either copy and the rules would stop being separable.
CHECK = ("Die Schutzeinrichtung nach Bild 7 wird vor der Inbetriebnahme geprüft und das "
         "Ergebnis im Anlagenpass vermerkt.")

#: Same length, no shared wording -- the filler that must never produce a hit.
FILLER = ("Die Bemessungsspannung der Kundenanlage richtet sich nach dem Anschlusspunkt "
          "und wird im Netzanschlussvertrag festgelegt.")


# -- miniature runs -----------------------------------------------------------------

def _para(pid: str, text: str) -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": "text"}


def _doc(doc_id: str, sections: list[tuple[str, str, list[str]]]) -> dict:
    return {"doc_id": doc_id, "source": {"format": "docx", "sha256": "0" * 8},
            "sections": [{"id": sid, "title": title, "level": 1, "part": "hauptteil",
                          "paragraphs": [_para(f"{sid}.p{i}", t)
                                         for i, t in enumerate(texts)],
                          "tables": [], "figures": [], "formulas": []}
                         for sid, title, texts in sections]}


def _chapter(mapping_id: str, old_ids: list[str], new_ids: list[str],
             removed: list[str]) -> dict:
    return {"mapping_id": mapping_id,
            "old_id": old_ids[0] if old_ids else None,
            "new_id": new_ids[0] if new_ids else None,
            "old_ids": old_ids, "new_ids": new_ids, "title": "Kapitel",
            "mode": "id+title", "map_confidence": 1.0,
            "changes": [{"kind": "removed", "confidence": 0.0,
                         "old_ids": [f"{old_ids[0]}.p{i}"], "new_ids": [],
                         "old_text": text, "new_text": None}
                        for i, text in enumerate(removed)]}


def _synopse(chapters: list[dict]) -> dict:
    return {"pair": "alt <-> neu", "old_doc": "alt", "new_doc": "neu",
            "stats": {}, "chapters": chapters}


def _b_case() -> tuple[dict, dict, dict]:
    """The text stands in ``12.1`` -- named in the mapping id, absent from ``new_ids``.

    ``12.1`` is deliberately *not* a descendant of the mapped section, so rule C cannot
    reach it and the two rules stay separable.
    """
    old_doc = _doc("alt", [("11.3", "Kapitel alt", [DUTY, FILLER])])
    new_doc = _doc("neu", [("11.3", "Zugeordnet", [FILLER]),
                           ("11.3.2", "Kind des Zugeordneten", [FILLER]),
                           ("12.1", "Nachbardatensatz", [FILLER, DUTY])])
    return old_doc, new_doc, _chapter("11.3+12.1<11.3", ["11.3"], ["11.3"], [DUTY])


def _c_case(text: str = DUTY) -> tuple[dict, dict, dict]:
    """The text stands in ``11.2.7.6`` -- a descendant of the mapped ``11.2.7``."""
    old_doc = _doc("alt", [("11.2.7", "Kapitel alt", [text, FILLER])])
    new_doc = _doc("neu", [("11.2.7", "Zugeordnet", [FILLER]),
                           ("11.2.7.6", "Kind", [FILLER, text]),
                           ("9", "Fremd", [FILLER])])
    return old_doc, new_doc, _chapter("11.2.7<11.2.7", ["11.2.7"], ["11.2.7"], [text])


def _write_run(tmp_path: Path, old_doc: dict, new_doc: dict, synopse: dict,
               name: str = "run") -> Path:
    run = tmp_path / name
    (run / "alt").mkdir(parents=True)
    (run / "neu").mkdir(parents=True)
    for path, data in ((run / "alt" / "norm_doc.json", old_doc),
                       (run / "neu" / "norm_doc.json", new_doc),
                       (run / "synopse.json", synopse)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return run


def _resolved(old_doc: dict, new_doc: dict, chapters: list[dict]) -> dict[str, int]:
    """``rule -> how many relocated cases the circle of that rule covers``."""
    result = candidate_simulation.simulate(_synopse(chapters), new_doc, old_doc)
    return {rule: result["rules"][rule]["resolved"] for rule in candidate_simulation.RULES}


# -- the four rules -------------------------------------------------------------------

def test_rule_b_finds_text_named_in_the_mapping_id():
    """``mapping_id`` names the merge group, ``new_ids`` the single record (AP-10)."""
    old_doc, new_doc, chapter = _b_case()
    result = candidate_simulation.simulate(_synopse([chapter]), new_doc, old_doc)

    assert result["relocated"] == 1
    assert result["relocated_verbatim"] == 1
    assert _resolved(old_doc, new_doc, [chapter]) == {"A": 0, "B": 1, "C": 0, "D": 1}
    assert result["rules"]["B"]["resolved_verbatim"] == 1
    assert result["rules"]["A"]["resolved_verbatim"] == 0


def test_rule_b_reads_both_sides_of_the_mapping_id():
    """The AP-10 proof case ``11.3<11.3+11.3.2``: the extra id sits on the *old* side.

    Two old sections were merged onto one new one, and the new edition carries a section
    of the absorbed number all the same -- that is where the text stands.
    """
    old_doc = _doc("alt", [("11.3", "Kapitel alt", [DUTY]),
                           ("11.3.2", "Aufgegangen", [FILLER])])
    new_doc = _doc("neu", [("11.3", "Zugeordnet", [FILLER]),
                           ("11.3.2", "Gleiche Nummer, neuer Inhalt", [DUTY])])
    chapter = _chapter("11.3<11.3+11.3.2", ["11.3", "11.3.2"], ["11.3"], [DUTY])

    assert candidate_simulation.mapping_id_sections("11.3<11.3+11.3.2") == \
        ["11.3", "11.3", "11.3.2"]
    assert _resolved(old_doc, new_doc, [chapter])["B"] == 1
    assert _resolved(old_doc, new_doc, [chapter])["A"] == 0


def test_rule_c_finds_a_descendant():
    """``11.2.7`` is mapped, the text stands in ``11.2.7.6`` (8 of the 32 AP-10 cases)."""
    old_doc, new_doc, chapter = _c_case()
    assert _resolved(old_doc, new_doc, [chapter]) == {"A": 0, "B": 0, "C": 1, "D": 1}


def test_rule_d_is_the_union():
    """Rule D covers a case wherever B or C covers it, and nothing is lost in between."""
    old_b, new_b, chapter_b = _b_case()
    old_c, new_c, chapter_c = _c_case(CHECK)
    old_doc = _doc("alt", [])
    old_doc["sections"] = old_b["sections"] + old_c["sections"]
    new_doc = _doc("neu", [])
    new_doc["sections"] = new_b["sections"] + new_c["sections"]

    assert _resolved(old_doc, new_doc, [chapter_b, chapter_c]) == \
        {"A": 0, "B": 1, "C": 1, "D": 2}


def test_simulation_reports_the_candidate_growth():
    """The cost side is counted, not estimated: paragraphs added to the circle."""
    old_doc, new_doc, chapter = _c_case()
    rules = candidate_simulation.simulate(_synopse([chapter]), new_doc, old_doc)["rules"]

    # 11.2.7 holds one paragraph, its descendant 11.2.7.6 holds two
    assert rules["A"]["circle_paragraphs_mean"] == 1.0
    assert rules["A"]["growth_max"] == 0
    assert rules["C"]["growth_max"] == 2
    assert rules["C"]["growth_mean"] == 2.0
    assert rules["C"]["circle_paragraphs_mean"] == 3.0
    # the foreign section 9 is in no circle
    assert rules["D"]["growth_max"] == 2


def test_rule_b_dissolves_the_record_boundary():
    """``12.1`` belongs to another record: rule B puts it into two circles at once."""
    old_b, new_b, chapter_b = _b_case()
    neighbour = _chapter("12.1<12.1", ["12.1"], ["12.1"], [])
    result = candidate_simulation.simulate(_synopse([chapter_b, neighbour]), new_b, old_b)

    assert result["rules"]["A"]["datasets_with_foreign_sections"] == 0
    assert result["rules"]["B"]["datasets_with_foreign_sections"] == 1
    assert result["rules"]["B"]["sections_in_several_circles"] == 1
    assert result["rules"]["C"]["sections_in_several_circles"] == 0


# -- what must not change -------------------------------------------------------------

def _snapshot(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def test_simulation_changes_nothing(tmp_path, capsys):
    """A measurement: the run directory and the aligner are read, never written."""
    old_doc, new_doc, chapter = _c_case()
    run = _write_run(tmp_path, old_doc, new_doc, _synopse([chapter]))
    before, align_before = _snapshot(run), _snapshot(ROOT / "src/normpare/stages/align")

    assert candidate_simulation.main(["--dir", str(run)]) == 0
    capsys.readouterr()

    assert _snapshot(run) == before
    assert _snapshot(ROOT / "src/normpare/stages/align") == align_before


def test_simulation_is_deterministic(tmp_path, capsys):
    """Twice the same run, character for character -- no timestamp, no dict order."""
    old_doc, new_doc, chapter = _c_case()
    run = _write_run(tmp_path, old_doc, new_doc, _synopse([chapter]))

    outs = []
    for name in ("one", "two"):
        dest = tmp_path / name
        assert candidate_simulation.main(
            ["--dir", str(run), "--out", str(dest), "--name", "mini"]) == 0
        outs.append((dest / "simulation_mini.txt").read_bytes())
    capsys.readouterr()

    assert outs[0] == outs[1]
    assert b"relocated" in outs[0]
