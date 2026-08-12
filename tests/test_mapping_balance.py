"""AP-09: plausibility of a chapter mapping, measured from its paragraph mass.

A mapping that pairs 89 old paragraphs with 9 new ones cannot be right: the Hungarian
assignment in ``align_chapter`` pairs one to one, so the old surplus is reported as
``removed`` no matter what the text says. ``paragraph_balance`` makes that mass ratio
visible on every chapter record, and ``from_unbalanced_mapping`` marks the deletions that
come out of such a mapping.

The stage is measured, not changed: no removal is suppressed, reweighted or reclassified.

All documents here are built in the test itself -- neither the synthetic corpus nor real
standard text, so the numbers stay readable.
"""
from __future__ import annotations

import json

from normpare.stages.diff import BALANCE_MIN_SIZE, UNBALANCED_MIN, build_synopse


# --- mini documents ---------------------------------------------------------------

def _para(pid: str, text: str, kind: str = "text") -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": kind, "modality": "informativ",
            "modality_counts": {}, "refs_internal": [], "refs_external": [], "values": []}


def _section(sid: str, title: str, paras: list[dict]) -> dict:
    return {"id": sid, "title": title, "level": 1, "part": "main",
            "paragraphs": paras, "tables": [], "figures": [], "formulas": []}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "sections": sections,
            "source": {"format": "docx", "sha256": "0" * 8}}


def _old_text(k: int) -> str:
    return f"Die Anlage nach Nummer {k} ist vor der Inbetriebnahme zu pruefen."


def _new_text(k: int) -> str:
    return f"Die Anlage nach Nummer {k} ist vor der Inbetriebnahme zu warten."


def _record(old_ids: list[str], new_ids: list[str], links: list[dict],
            match_type: str = "id+title", confidence: float = 1.0) -> dict:
    return {"old_id": old_ids[0] if old_ids else None,
            "new_id": new_ids[0] if new_ids else None,
            "old_ids": old_ids, "new_ids": new_ids,
            "old_title": "Kapitel alt", "new_title": "Kapitel neu",
            "old_level": 1, "new_level": 1, "old_part": "main", "new_part": "main",
            "match_type": match_type, "confidence": confidence,
            "part_changed": False, "para_links": links}


def _case(n_old: int, n_new: int) -> tuple[dict, dict, list[dict]]:
    """One chapter mapping with ``n_old`` old and ``n_new`` new paragraphs.

    Paired one to one as far as both sides reach; the old surplus becomes ``removed``,
    the new surplus ``new`` -- exactly what the Hungarian assignment leaves behind.
    """
    olds = [_para(f"a.p{i}", _old_text(i)) for i in range(n_old)]
    news = [_para(f"n.p{j}", _new_text(j)) for j in range(n_new)]
    links = [{"old_ids": [f"a.p{k}"], "new_ids": [f"n.p{k}"], "kind": "similar",
              "confidence": 0.9} for k in range(min(n_old, n_new))]
    links += [{"old_ids": [f"a.p{k}"], "new_ids": [], "kind": "removed", "confidence": 0.0}
              for k in range(n_new, n_old)]
    links += [{"old_ids": [], "new_ids": [f"n.p{k}"], "kind": "new", "confidence": 0.0}
              for k in range(n_old, n_new)]
    return (_doc("alt", [_section("A", "Kapitel alt", olds)]),
            _doc("neu", [_section("N", "Kapitel neu", news)]),
            [_record(["A"], ["N"], links)])


def _synopse(tmp_path, old_doc, new_doc, records) -> dict:
    return build_synopse(old_doc, new_doc, records, None,
                         tmp_path / "synopse.json", "alt <-> neu")


def _balance(tmp_path, n_old: int, n_new: int) -> dict:
    old_doc, new_doc, records = _case(n_old, n_new)
    return _synopse(tmp_path, old_doc, new_doc, records)["chapters"][0]["paragraph_balance"]


# --- the metric -------------------------------------------------------------------

def test_balance_is_zero_for_equal_sides(tmp_path):
    b = _balance(tmp_path, 20, 20)
    assert b["n_old"] == 20 and b["n_new"] == 20
    assert b["imbalance"] == 0.0
    assert b["old_surplus"] == 0
    assert b["unbalanced"] is False


def test_balance_detects_the_60909_case(tmp_path):
    # 7.2 < 9.1+9.1.1+9.1.3+... in out/60909_2026-08: 89 old paragraphs against 9 new
    b = _balance(tmp_path, 89, 9)
    assert b["n_old"] == 89 and b["n_new"] == 9
    assert b["imbalance"] == 0.8989
    assert b["old_surplus"] == 80
    assert b["unbalanced"] is True


def test_balance_handles_an_empty_new_side(tmp_path):
    # 7 < 7+7.2+7.2.2+...: 33 old paragraphs against none at all
    b = _balance(tmp_path, 33, 0)
    assert b["n_old"] == 33 and b["n_new"] == 0
    assert b["imbalance"] == 1.0
    assert b["old_surplus"] == 33
    assert b["unbalanced"] is True


def test_balance_handles_two_empty_sides(tmp_path):
    b = _balance(tmp_path, 0, 0)
    assert b == {"n_old": 0, "n_new": 0, "imbalance": 0.0, "old_surplus": 0,
                 "unbalanced": False}


def test_small_mappings_stay_unmarked(tmp_path):
    # fully imbalanced, but too small to mean anything
    assert BALANCE_MIN_SIZE > 4
    old_doc, new_doc, records = _case(4, 0)
    ch = _synopse(tmp_path, old_doc, new_doc, records)["chapters"][0]
    assert ch["paragraph_balance"]["imbalance"] == 1.0
    assert ch["paragraph_balance"]["unbalanced"] is False
    assert [c for c in ch["changes"] if c["kind"] == "removed"]
    assert all("from_unbalanced_mapping" not in c for c in ch["changes"])


def test_every_chapter_carries_the_field(tmp_path):
    olds = [_para(f"a.p{i}", _old_text(i)) for i in range(6)]
    news = [_para(f"n.p{j}", _new_text(j)) for j in range(6)]
    gone = [_para("g.p0", _old_text(90))]
    fresh = [_para("f.p0", _new_text(91))]
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds), _section("G", "Entfallen", gone)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news), _section("F", "Neu", fresh)])
    links = [{"old_ids": [f"a.p{k}"], "new_ids": [f"n.p{k}"], "kind": "similar",
              "confidence": 0.9} for k in range(6)]
    records = [
        _record(["A"], ["N"], links),
        {"old_id": "G", "new_id": None, "old_ids": ["G"], "new_ids": [],
         "old_title": "Entfallen", "old_level": 1, "old_part": "main",
         "match_type": "removed", "confidence": 0.0, "part_changed": False},
        {"old_id": None, "new_id": "F", "old_ids": [], "new_ids": ["F"],
         "new_title": "Neu", "new_level": 1, "new_part": "main",
         "match_type": "new", "confidence": 0.0, "part_changed": False},
    ]
    syn = _synopse(tmp_path, old_doc, new_doc, records)
    assert [ch["mode"] for ch in syn["chapters"]] == ["id+title", "removed", "new"]
    for ch in syn["chapters"]:
        assert "paragraph_balance" in ch, ch["mapping_id"]
    assert syn["chapters"][2]["paragraph_balance"] == {
        "n_old": 0, "n_new": 1, "imbalance": 1.0, "old_surplus": 0, "unbalanced": False}


# --- the mark ---------------------------------------------------------------------

def test_only_removals_get_the_flag(tmp_path):
    olds = ([_para("a.p0", _old_text(0)),
             _para("a.p1", "Die Anlage ist zu pruefen (siehe Abschnitt 5).")]
            + [_para(f"a.p{i}", _old_text(i)) for i in range(2, 12)])
    news = [_para("n.p0", _new_text(0)),
            _para("n.p1", "Die Anlage ist zu pruefen [siehe Abschnitt 5]."),
            _para("n.p2", _new_text(92))]
    links = [
        {"old_ids": ["a.p0"], "new_ids": ["n.p0"], "kind": "similar", "confidence": 0.9},
        {"old_ids": ["a.p1"], "new_ids": ["n.p1"], "kind": "similar", "confidence": 0.9},
        {"old_ids": [], "new_ids": ["n.p2"], "kind": "new", "confidence": 0.0},
    ] + [{"old_ids": [f"a.p{i}"], "new_ids": [], "kind": "removed", "confidence": 0.0}
         for i in range(2, 12)]
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    ch = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])["chapters"][0]

    assert ch["paragraph_balance"]["unbalanced"] is True
    kinds = {c["kind"] for c in ch["changes"]}
    assert {"removed", "new", "similar", "cosmetic"} <= kinds
    for c in ch["changes"]:
        if c["kind"] == "removed":
            assert c["from_unbalanced_mapping"] is True
        else:
            assert "from_unbalanced_mapping" not in c


def test_the_flag_is_absent_when_false(tmp_path):
    olds = [_para(f"a.p{i}", _old_text(i)) for i in range(10)]
    news = [_para(f"n.p{j}", _new_text(j)) for j in range(9)]
    links = [{"old_ids": [f"a.p{k}"], "new_ids": [f"n.p{k}"], "kind": "similar",
              "confidence": 0.9} for k in range(9)]
    links.append({"old_ids": ["a.p9"], "new_ids": [], "kind": "removed", "confidence": 0.0})
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    out = tmp_path / "synopse.json"
    ch = build_synopse(old_doc, new_doc, [_record(["A"], ["N"], links)], None,
                       out, "alt <-> neu")["chapters"][0]

    assert ch["paragraph_balance"]["imbalance"] < UNBALANCED_MIN
    removed = [c for c in ch["changes"] if c["kind"] == "removed"]
    assert len(removed) == 1
    assert "from_unbalanced_mapping" not in removed[0]
    assert "from_unbalanced_mapping" not in out.read_text(encoding="utf-8")


def test_formula_paragraphs_do_not_count(tmp_path):
    # formulas go through formulas_diff, not through paragraph alignment -- so they must
    # not inflate the paragraph mass either
    olds = ([_para(f"a.p{i}", _old_text(i)) for i in range(6)]
            + [_para(f"a.f{i}", f"I_k = {i} kA", kind="formula") for i in range(20)])
    news = [_para(f"n.p{j}", _new_text(j)) for j in range(6)]
    links = [{"old_ids": [f"a.p{k}"], "new_ids": [f"n.p{k}"], "kind": "similar",
              "confidence": 0.9} for k in range(6)]
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    ch = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])["chapters"][0]

    assert ch["paragraph_balance"] == {"n_old": 6, "n_new": 6, "imbalance": 0.0,
                                       "old_surplus": 0, "unbalanced": False}


def test_synopse_stays_deterministic(tmp_path):
    old_doc, new_doc, records = _case(89, 9)
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    build_synopse(old_doc, new_doc, json.loads(json.dumps(records)), None, first, "alt <-> neu")
    build_synopse(old_doc, new_doc, json.loads(json.dumps(records)), None, second, "alt <-> neu")
    assert first.read_bytes() == second.read_bytes()
