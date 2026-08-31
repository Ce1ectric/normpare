"""AP-30: a move into a chapter without a counterpart is written on both sides.

``mapping.json`` lists every move the document-wide pass found under ``moved``; the
``para_links`` say what was actually written down. Both lists came apart wherever one side
of a move sat in a chapter with no counterpart: those records got no ``para_links`` at all,
their paragraphs entered the pass through a second route without a link index, and the
writing step silently skipped the side it could not address. The consequence is one event
counted twice -- once as a move, once as an addition -- or, when both sides were unmapped,
not written at all.

What must **not** change is the pairing: the candidate lists, the similarity matrix and the
assignment stay as they are, so ``align_document`` returns the same ``moved`` list as
before. Only the writing changes. Two tests here are therefore invariants that were green
before the implementation, by design.

No network and no model: the similarity backend is a table lookup.
"""
from __future__ import annotations

import numpy as np
import pytest

from normpare.stages.align import paras as paras_mod
from normpare.stages.align.paras import align_document
from normpare.stages.deutung import move_pairs
from normpare.stages.diff import build_synopse


# --- solver, backend and mini documents -------------------------------------------

def _greedy_lsa(cost):
    """Deterministic greedy assignment (SciPy-free), as in ``test_moves``."""
    cost = np.asarray(cost)
    n, m = cost.shape
    order = sorted((cost[i, j], i, j) for i in range(n) for j in range(m))
    ur, uc, rr, cc = set(), set(), [], []
    for _, i, j in order:
        if i in ur or j in uc:
            continue
        ur.add(i); uc.add(j); rr.append(i); cc.append(j)
    idx = sorted(range(len(rr)), key=lambda k: rr[k])
    return np.array([rr[k] for k in idx]), np.array([cc[k] for k in idx])


@pytest.fixture(autouse=True)
def _patch_lsa(monkeypatch):
    monkeypatch.setattr(paras_mod, "_lsa", _greedy_lsa)


class TagBackend:
    """Similarity from an explicit table, keyed by the first token of each text.

    Records every call, so a test can state which candidates the pass saw and in which
    order -- the order decides the matrix and with it the assignment.
    """

    def __init__(self, table: dict, default: float = 0.0):
        self.table, self.default = table, default
        self.calls: list[tuple[list[str], list[str]]] = []

    def sim_matrix(self, texts_a, texts_b):
        tag = lambda t: t.split()[0]
        self.calls.append(([tag(t) for t in texts_a], [tag(t) for t in texts_b]))
        return np.array([[self.table.get((tag(x), tag(y)), self.default) for y in texts_b]
                         for x in texts_a], dtype=float)


def _text(tag: str, words: int = 20) -> str:
    return f"{tag} " + "wort " * words


def _para(pid: str, words: int = 20) -> dict:
    t = _text(pid, words)
    return {"id": pid, "n0": t, "n1": t, "kind": "text"}


def _section(sid: str, paras: list[dict]) -> dict:
    return {"id": sid, "title": f"Kapitel {sid}", "level": 1, "part": "main",
            "paragraphs": paras, "tables": [], "figures": [], "formulas": []}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "sections": sections,
            "source": {"format": "docx", "sha256": "0" * 8}}


def _record(old_id: str | None, new_id: str | None,
            match_type: str = "id+title") -> dict:
    return {"old_id": old_id, "new_id": new_id,
            "old_ids": [old_id] if old_id else [], "new_ids": [new_id] if new_id else [],
            "old_title": "alt", "new_title": "neu", "match_type": match_type,
            "mapping_id": f"{old_id or ''}<{new_id or ''}", "confidence": 1.0}


def _links(rec: dict) -> list[dict]:
    return rec.get("para_links") or []


def _kinds(rec: dict) -> list[str]:
    return [l["kind"] for l in _links(rec)]


def _run(old_doc, new_doc, records, backend, **kw):
    return align_document(old_doc, new_doc, records, backend,
                          tau_moved=0.72, tau_moved_short=0.90, **kw)


# --- part A: chapters without a counterpart get para_links -------------------------

def test_a_new_chapter_record_gets_para_links():
    old_doc = _doc("alt", [_section("A", [_para("oA1")])])
    new_doc = _doc("neu", [_section("A", []), _section("N", [_para("nN1"),
                                                            _para("nN2")])])
    records = [_record("A", "A"), _record(None, "N", match_type="new")]
    _run(old_doc, new_doc, records, TagBackend({}))

    assert _links(records[1]) == [
        {"old_ids": [], "new_ids": ["nN1"], "kind": "new", "confidence": 0.0},
        {"old_ids": [], "new_ids": ["nN2"], "kind": "new", "confidence": 0.0}]


def test_a_removed_chapter_record_gets_para_links():
    old_doc = _doc("alt", [_section("A", []), _section("R", [_para("oR1"),
                                                            _para("oR2")])])
    new_doc = _doc("neu", [_section("A", [_para("nA1")])])
    records = [_record("A", "A"), _record("R", None, match_type="removed")]
    _run(old_doc, new_doc, records, TagBackend({}))

    assert _links(records[1]) == [
        {"old_ids": ["oR1"], "new_ids": [], "kind": "removed", "confidence": 0.0},
        {"old_ids": ["oR2"], "new_ids": [], "kind": "removed", "confidence": 0.0}]


# --- the pairing must not move ----------------------------------------------------

def _order_fixture():
    """An unmapped chapter *before* the mapped one in the record order.

    Before AP-30 the paragraphs of chapters without a counterpart were appended behind all
    others; whoever sorts them into the main loop instead changes the matrix.
    """
    old_doc = _doc("alt", [_section("A", [_para("oA1")]), _section("R", [_para("oR1")])])
    new_doc = _doc("neu", [_section("A", [_para("nA1")]), _section("N", [_para("nN1")])])
    records = [_record(None, "N", match_type="new"), _record("A", "A"),
               _record("R", None, match_type="removed")]
    return old_doc, new_doc, records


def test_the_candidate_order_is_unchanged():
    old_doc, new_doc, records = _order_fixture()
    backend = TagBackend({})
    _run(old_doc, new_doc, records, backend)

    # the last call is the document-wide moved pass: mapped chapters first, in record
    # order, the chapters without a counterpart behind them
    assert backend.calls[-1] == (["oA1", "oR1"], ["nA1", "nN1"])


def test_the_move_list_is_unchanged():
    """``moved`` element for element, against the list the pass returned before AP-30."""
    old_doc, new_doc, records = _order_fixture()
    backend = TagBackend({("oA1", "nN1"): 0.95, ("oR1", "nA1"): 0.88})
    moved = _run(old_doc, new_doc, records, backend)

    assert [(m["old_id"], m["new_id"], m["confidence"], m["via"]) for m in moved] == [
        ("oA1", "nN1", 0.95, "tfidf"), ("oR1", "nA1", 0.88, "tfidf")]


# --- both sides of a move get a record --------------------------------------------

def _into_new_chapter():
    old_doc = _doc("alt", [_section("A", [_para("oA1")])])
    new_doc = _doc("neu", [_section("A", []), _section("N", [_para("nN1")])])
    records = [_record("A", "A"), _record(None, "N", match_type="new")]
    return old_doc, new_doc, records, TagBackend({("oA1", "nN1"): 0.95})


def _out_of_removed_chapter():
    old_doc = _doc("alt", [_section("B", []), _section("R", [_para("oR1")])])
    new_doc = _doc("neu", [_section("B", [_para("nB1")])])
    records = [_record("B", "B"), _record("R", None, match_type="removed")]
    return old_doc, new_doc, records, TagBackend({("oR1", "nB1"): 0.93})


def _between_unmapped_chapters():
    old_doc = _doc("alt", [_section("R", [_para("oR1")])])
    new_doc = _doc("neu", [_section("N", [_para("nN1")])])
    records = [_record("R", None, match_type="removed"),
               _record(None, "N", match_type="new")]
    return old_doc, new_doc, records, TagBackend({("oR1", "nN1"): 0.91})


def test_a_move_into_a_new_chapter_is_written_on_both_sides():
    old_doc, new_doc, records, backend = _into_new_chapter()
    moved = _run(old_doc, new_doc, records, backend)

    assert [(m["old_id"], m["new_id"]) for m in moved] == [("oA1", "nN1")]
    assert _kinds(records[0]) == ["moved_away"]
    assert _kinds(records[1]) == ["moved_in"]
    assert _links(records[1])[0]["moved_from"] == "oA1"
    assert _links(records[1])[0]["confidence"] == 0.95


def test_a_move_out_of_a_removed_chapter_is_written_on_both_sides():
    old_doc, new_doc, records, backend = _out_of_removed_chapter()
    moved = _run(old_doc, new_doc, records, backend)

    assert [(m["old_id"], m["new_id"]) for m in moved] == [("oR1", "nB1")]
    assert _kinds(records[1]) == ["moved_away"]
    assert _links(records[1])[0]["moved_to"] == "nB1"
    assert _kinds(records[0]) == ["moved_in"]


def test_a_move_between_two_unmapped_chapters_is_written():
    old_doc, new_doc, records, backend = _between_unmapped_chapters()
    moved = _run(old_doc, new_doc, records, backend)

    assert [(m["old_id"], m["new_id"]) for m in moved] == [("oR1", "nN1")]
    assert _kinds(records[0]) == ["moved_away"]
    assert _kinds(records[1]) == ["moved_in"]


def test_the_pointer_matches_the_moved_in_record():
    old_doc, new_doc, records, backend = _into_new_chapter()
    _run(old_doc, new_doc, records, backend)

    away = _links(records[0])[0]
    into = _links(records[1])[0]
    assert away["moved_to"] == into["new_ids"][0]
    assert into["moved_from"] == away["old_ids"][0]


# --- part B: a pointer is evidence or it is not written ---------------------------

def test_a_move_without_a_target_record_keeps_only_the_chapter():
    # imported here: the writer is the piece AP-30 adds, and the rest of this module
    # must stay collectable while it does not exist yet
    from normpare.stages.align.paras import _write_move

    away ={"old_ids": ["oA1"], "new_ids": [], "kind": "removed", "confidence": 0.0}
    _write_move(away, None, {"id": "oA1"}, {"id": "nN1"}, 0.95, "tfidf", {"nN1": "N"})

    assert away["kind"] == "moved_away"
    assert "moved_to" not in away          # nothing on the new side proves the paragraph
    assert away["moved_to_chapter"] == "N"
    assert away["via"] == "tfidf" and away["confidence"] == 0.95


# --- the whole way through, up to the synopse -------------------------------------

def _mini_run(tmp_path):
    """All three defect shapes in one document: into a new chapter, out of a removed one,
    and between two chapters that both have no counterpart."""
    old_doc = _doc("alt", [_section("A", [_para("oA1")]),
                           _section("B", []),
                           _section("R", [_para("oR1"), _para("oR2")])])
    new_doc = _doc("neu", [_section("A", []),
                           _section("B", [_para("nB1")]),
                           _section("N", [_para("nN1"), _para("nN2")])])
    records = [_record("A", "A"), _record("B", "B"),
               _record("R", None, match_type="removed"),
               _record(None, "N", match_type="new")]
    backend = TagBackend({("oA1", "nN1"): 0.95, ("oR1", "nB1"): 0.93,
                          ("oR2", "nN2"): 0.91})
    moved = _run(old_doc, new_doc, records, backend)
    synopse = build_synopse(old_doc, new_doc, records, backend,
                            tmp_path / "synopse.json", "alt <-> neu")
    return moved, records, synopse


def test_no_dead_pointer_remains(tmp_path):
    moved, records, _ = _mini_run(tmp_path)
    assert len(moved) == 3

    into = {l["moved_from"] for rec in records for l in _links(rec)
            if l["kind"] == "moved_in"}
    away = [l for rec in records for l in _links(rec) if l["kind"] == "moved_away"]
    assert len(away) == 3
    assert all(l.get("moved_to") for l in away)      # every pointer is set ...
    assert into == {"oA1", "oR1", "oR2"}             # ... and every one of them is held


def test_move_pairs_reports_zero_dead_pointers(tmp_path):
    _, _, synopse = _mini_run(tmp_path)
    joined = move_pairs(synopse)

    assert joined["dangling"] == []
    assert joined["unpaired"] == []
    assert len(joined["pairs"]) == 3


def test_an_older_mapping_stays_readable(tmp_path):
    """A ``mapping.json`` from before AP-30: chapters without a counterpart carry no
    ``para_links``. The synopse still reports their paragraphs as new and removed."""
    old_doc = _doc("alt", [_section("R", [_para("oR1")])])
    new_doc = _doc("neu", [_section("N", [_para("nN1")])])
    records = [_record("R", None, match_type="removed"),
               _record(None, "N", match_type="new")]
    synopse = build_synopse(old_doc, new_doc, records, TagBackend({}),
                            tmp_path / "synopse.json", "alt <-> neu")

    kinds = [c["kind"] for ch in synopse["chapters"] for c in ch["changes"]]
    assert kinds == ["removed", "new"]
    assert move_pairs(synopse) == {"pairs": [], "unpaired": [], "dangling": []}
