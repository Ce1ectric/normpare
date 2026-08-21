"""AP-26: a paragraph that moved is not a paragraph that is gone -- but only with evidence.

Three passes are exercised here, and they differ in what they are allowed to claim:

* **block continuation** (part A) argues from context, not from text: a removed paragraph
  wedged between two paragraphs that moved into the *same* new chapter moved with them.
  It knows the target chapter and nothing more, so it writes ``moved_to_chapter`` and
  deliberately not ``moved_to``.
* **short paragraphs** (part B) enter the deterministic ``moved`` pass from 40 characters
  instead of 80, and pay for it with a higher similarity threshold.
* **cross-chapter embedding moves** (part C) need all three conditions -- similarity,
  value consistency and a mutually best pairing. A pair that clears the similarity but
  fails one of the other two is *not* a move; it stays a removal and says why.

No network and no model: the embedding backend is a fake with an explicit matrix.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from normpare.stages.align import paras as paras_mod
from normpare.stages.align.paras import (
    align_document,
    block_continuation_pass,
    embed_move_pass,
)
from normpare.stages.review_removed import review_entries


# --- solver, backends and mini documents ------------------------------------------

def _greedy_lsa(cost):
    """Deterministic greedy assignment (SciPy-free), as in ``test_embed_rescue``.

    Every matrix in this file is chosen so that greedy and Hungarian agree, so the tests
    say the same thing with either solver.
    """
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
    """Similarity from an explicit table, keyed by the first token of each text."""

    def __init__(self, table: dict, default: float = 0.0):
        self.table, self.default = table, default

    def sim_matrix(self, texts_a, texts_b):
        tag = lambda t: t.split()[0]
        return np.array([[self.table.get((tag(x), tag(y)), self.default) for y in texts_b]
                         for x in texts_a], dtype=float)


def _text(tag: str, words: int, extra: str = "") -> str:
    """A text starting with ``tag`` (the backend key), padded to a known length."""
    return f"{tag} " + "wort " * words + extra


def _para(pid: str, text: str) -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": "text"}


def _section(sid: str, paras: list[dict]) -> dict:
    return {"id": sid, "title": f"Kapitel {sid}", "level": 1, "part": "main",
            "paragraphs": paras, "tables": [], "figures": [], "formulas": []}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "sections": sections,
            "source": {"format": "docx", "sha256": "0" * 8}}


def _record(old_id: str | None, new_id: str | None, links: list[dict],
            match_type: str = "id+title") -> dict:
    return {"old_id": old_id, "new_id": new_id,
            "old_ids": [old_id] if old_id else [], "new_ids": [new_id] if new_id else [],
            "old_title": "alt", "new_title": "neu", "match_type": match_type,
            "confidence": 1.0, "para_links": links}


def _removed(old_id: str) -> dict:
    return {"old_ids": [old_id], "new_ids": [], "kind": "removed", "confidence": 0.0}


def _new(new_id: str) -> dict:
    return {"old_ids": [], "new_ids": [new_id], "kind": "new", "confidence": 0.0}


def _moved_away(old_id: str, target: str) -> dict:
    return {"old_ids": [old_id], "new_ids": [], "kind": "moved_away", "moved_to": target,
            "confidence": 0.9, "via": "tfidf"}


# --- part A: block continuation ---------------------------------------------------

def _block_fixture(second_target: str = "nX2"):
    """Old chapter A: three paragraphs, the outer two moved to the chapter of the targets."""
    old_doc = _doc("alt", [_section("A", [_para("oA1", _text("oA1", 20)),
                                          _para("oA2", _text("oA2", 3)),
                                          _para("oA3", _text("oA3", 20))])])
    new_doc = _doc("neu", [_section("X", [_para("nX1", _text("nX1", 20)),
                                          _para("nX2", _text("nX2", 20))]),
                           _section("Y", [_para("nY1", _text("nY1", 20))])])
    records = [_record("A", "A", [_moved_away("oA1", "nX1"),
                                  _removed("oA2"),
                                  _moved_away("oA3", second_target)])]
    return old_doc, new_doc, records


def test_a_paragraph_between_two_moves_follows_them():
    old_doc, new_doc, records = _block_fixture()
    filled = block_continuation_pass(old_doc, new_doc, records)

    link = records[0]["para_links"][1]
    assert link["kind"] == "moved_away"
    assert link["via"] == "block"
    assert [f["old_id"] for f in filled] == ["oA2"]
    assert filled[0]["chapter"] == "X" and filled[0]["via"] == "block"


def test_the_block_rule_needs_the_same_target_chapter():
    # the neighbour behind it moved into chapter Y, the one before it into X -- no block
    old_doc, new_doc, records = _block_fixture(second_target="nY1")
    filled = block_continuation_pass(old_doc, new_doc, records)

    assert filled == []
    assert records[0]["para_links"][1]["kind"] == "removed"
    assert "moved_to_chapter" not in records[0]["para_links"][1]


def test_the_block_rule_reports_only_the_chapter():
    old_doc, new_doc, records = _block_fixture()
    block_continuation_pass(old_doc, new_doc, records)

    link = records[0]["para_links"][1]
    assert link["moved_to_chapter"] == "X"
    assert "moved_to" not in link          # the chapter is proven, the paragraph is not
    assert link["new_ids"] == []


def test_a_paragraph_outside_a_block_stays_removed():
    """A removal with a moved neighbour on one side only keeps its verdict."""
    old_doc = _doc("alt", [_section("A", [_para("oA1", _text("oA1", 20)),
                                          _para("oA2", _text("oA2", 3))])])
    new_doc = _doc("neu", [_section("X", [_para("nX1", _text("nX1", 20))])])
    records = [_record("A", "A", [_moved_away("oA1", "nX1"), _removed("oA2")])]

    assert block_continuation_pass(old_doc, new_doc, records) == []
    assert records[0]["para_links"][1]["kind"] == "removed"


# --- part B: short paragraphs in the deterministic pass ----------------------------

def _moved_fixture(old_words: int, new_words: int):
    """Old chapter A holds one paragraph, new chapter B one -- the only candidate pair."""
    old_doc = _doc("alt", [_section("A", [_para("oA1", _text("oA1", old_words))]),
                           _section("B", [])])
    new_doc = _doc("neu", [_section("A", []),
                           _section("B", [_para("nB1", _text("nB1", new_words))])])
    records = [_record("A", "A", [_removed("oA1")]),
               _record("B", "B", [_new("nB1")])]
    return old_doc, new_doc, records


def test_short_paragraphs_need_a_higher_threshold():
    backend = TagBackend({("oA1", "nB1"): 0.80})
    # ~50 characters: seen by the pass since AP-26, but 0.80 does not carry a short claim
    old_doc, new_doc, records = _moved_fixture(8, 8)
    moved = align_document(old_doc, new_doc, records, backend,
                           tau_moved=0.72, tau_moved_short=0.90)

    assert moved == []
    assert records[0]["para_links"][0]["kind"] == "removed"

    # the same score on long paragraphs is a move: the length decides, not the score
    old_doc, new_doc, records = _moved_fixture(20, 20)
    moved = align_document(old_doc, new_doc, records, backend,
                           tau_moved=0.72, tau_moved_short=0.90)

    assert [m["old_id"] for m in moved] == ["oA1"]
    assert records[0]["para_links"][0]["kind"] == "moved_away"
    assert records[0]["para_links"][0]["moved_to"] == "nB1"
    assert records[0]["para_links"][0]["via"] == "tfidf"
    assert records[1]["para_links"][0]["kind"] == "moved_in"


def test_a_short_pair_above_the_short_threshold_moves():
    backend = TagBackend({("oA1", "nB1"): 0.95})
    old_doc, new_doc, records = _moved_fixture(8, 8)
    moved = align_document(old_doc, new_doc, records, backend,
                           tau_moved=0.72, tau_moved_short=0.90)

    assert [m["old_id"] for m in moved] == ["oA1"]
    assert records[0]["para_links"][0]["kind"] == "moved_away"


def test_a_paragraph_below_the_minimum_length_is_not_looked_at():
    backend = TagBackend({("oA1", "nB1"): 0.99})
    old_doc, new_doc, records = _moved_fixture(2, 2)      # under 40 characters
    assert align_document(old_doc, new_doc, records, backend,
                          tau_moved=0.72, tau_moved_short=0.90) == []
    assert records[0]["para_links"][0]["kind"] == "removed"


# --- part C: cross-chapter moves with a check rule ---------------------------------

def _cross_fixture(old_text: str, new_text: str):
    old_doc = _doc("alt", [_section("A", [_para("oA1", old_text)])])
    new_doc = _doc("neu", [_section("B", [_para("nB1", new_text)])])
    records = [_record("A", "A", [_removed("oA1")]),
               _record("B", "B", [_new("nB1")])]
    return old_doc, new_doc, records


PLAIN_OLD = _text("oA1", 20)
PLAIN_NEW = _text("nB1", 20)
#: The same requirement with the same limit -- a move.
VALUE_OLD = _text("oA1", 18, "Die Batterie ist fuer mindestens 8 h auszulegen.")
VALUE_SAME = _text("nB1", 18, "Die Batterie ist fuer mindestens 8 h auszulegen.")
#: The same sentence with another limit -- not a move, the content changed.
VALUE_OTHER = _text("nB1", 18, "Die Batterie ist fuer mindestens 4 h auszulegen.")


def test_a_cross_chapter_move_needs_all_three_conditions():
    # all three hold -> a move
    old_doc, new_doc, records = _cross_fixture(VALUE_OLD, VALUE_SAME)
    moves = embed_move_pass(old_doc, new_doc, records,
                            TagBackend({("oA1", "nB1"): 0.93}), tau_cross=0.88)
    assert [m["old_id"] for m in moves] == ["oA1"]
    assert records[0]["para_links"][0]["kind"] == "moved_away"

    # (1) similarity below the threshold -> nothing at all, not even a note
    old_doc, new_doc, records = _cross_fixture(VALUE_OLD, VALUE_SAME)
    assert embed_move_pass(old_doc, new_doc, records,
                           TagBackend({("oA1", "nB1"): 0.80}), tau_cross=0.88) == []
    assert records[0]["para_links"][0]["kind"] == "removed"
    assert "possible_move_to" not in records[0]["para_links"][0]

    # (2) a value of the old side is missing on the new one
    old_doc, new_doc, records = _cross_fixture(VALUE_OLD, VALUE_OTHER)
    assert embed_move_pass(old_doc, new_doc, records,
                           TagBackend({("oA1", "nB1"): 0.93}), tau_cross=0.88) == []
    assert records[0]["para_links"][0]["kind"] == "removed"


def test_a_value_mismatch_blocks_the_move():
    old_doc, new_doc, records = _cross_fixture(VALUE_OLD, VALUE_OTHER)
    moves = embed_move_pass(old_doc, new_doc, records,
                            TagBackend({("oA1", "nB1"): 0.93}), tau_cross=0.88)

    assert moves == []
    link = records[0]["para_links"][0]
    assert link["kind"] == "removed"
    assert records[1]["para_links"][0]["kind"] == "new"
    assert link["possible_move_to"] == {"new_id": "nB1", "chapter": "B",
                                        "similarity": 0.93, "failed": ["kennwerte"]}


def test_a_paragraph_without_values_passes_condition_two():
    old_doc, new_doc, records = _cross_fixture(PLAIN_OLD, PLAIN_NEW)
    moves = embed_move_pass(old_doc, new_doc, records,
                            TagBackend({("oA1", "nB1"): 0.93}), tau_cross=0.88)

    assert [m["old_id"] for m in moves] == ["oA1"]
    link = records[0]["para_links"][0]
    assert link["kind"] == "moved_away" and link["moved_to"] == "nB1"
    assert link["via"] == "embed" and link["moved_to_chapter"] == "B"
    assert records[1]["para_links"][0]["kind"] == "moved_in"
    assert records[1]["para_links"][0]["moved_from"] == "oA1"


def test_the_pair_must_be_mutually_best():
    old_doc = _doc("alt", [_section("A", [_para("oA1", _text("oA1", 20)),
                                          _para("oA2", _text("oA2", 20))])])
    new_doc = _doc("neu", [_section("B", [_para("nB1", _text("nB1", 20)),
                                          _para("nB2", _text("nB2", 20))])])
    records = [_record("A", "A", [_removed("oA1"), _removed("oA2")]),
               _record("B", "B", [_new("nB1"), _new("nB2")])]
    # the assignment pairs oA1 with nB2, but oA1 itself likes nB1 better
    backend = TagBackend({("oA1", "nB1"): 0.90, ("oA1", "nB2"): 0.88,
                          ("oA2", "nB1"): 0.95, ("oA2", "nB2"): 0.30})
    moves = embed_move_pass(old_doc, new_doc, records, backend, tau_cross=0.85)

    assert [m["old_id"] for m in moves] == ["oA2"]        # oA2 <-> nB1 is mutually best
    rejected = records[0]["para_links"][0]
    assert rejected["kind"] == "removed"
    assert rejected["possible_move_to"]["new_id"] == "nB2"
    assert rejected["possible_move_to"]["failed"] == ["wechselseitig"]


def test_a_rejected_pair_enters_the_review_list():
    synopse = {"pair": "alt <-> neu", "chapters": [{
        "mapping_id": "A", "new_id": "A", "title": "Kapitel A",
        "paragraph_balance": {"old_surplus": 0},
        "changes": [{"kind": "removed", "old_ids": ["oA1"], "old_text": VALUE_OLD,
                     "possible_move_to": {"new_id": "nB1", "chapter": "B",
                                          "similarity": 0.93, "failed": ["kennwerte"]}}],
    }]}
    entries = review_entries(synopse)

    assert len(entries) == 1
    assert "possible_move" in entries[0]["review_reasons"]
    assert entries[0]["possible_move"]["chapter"] == "B"


def test_the_embed_pass_is_skipped_without_the_backend():
    old_doc, new_doc, records = _cross_fixture(PLAIN_OLD, PLAIN_NEW)
    before = json.dumps(records, ensure_ascii=False)

    assert embed_move_pass(old_doc, new_doc, records, None, tau_cross=0.88) == []
    assert json.dumps(records, ensure_ascii=False) == before


def test_moves_are_deterministic():
    """All three passes over the same construction twice: same order, same values."""
    def _run():
        old_doc = _doc("alt", [
            _section("A", [_para("oA1", _text("oA1", 20)),
                           _para("oA2", _text("oA2", 3)),
                           _para("oA3", _text("oA3", 20))]),
            _section("D", [_para("oD1", _text("oD1", 20))]),
        ])
        new_doc = _doc("neu", [
            _section("X", [_para("nX1", _text("nX1", 20)),
                           _para("nX2", _text("nX2", 20))]),
            _section("E", [_para("nE1", _text("nE1", 20))]),
        ])
        records = [_record("A", "A", []), _record("X", "X", []),
                   _record("D", "D", []), _record("E", "E", [])]
        tfidf = TagBackend({("oA1", "nX1"): 0.93, ("oA3", "nX2"): 0.91})
        embed = TagBackend({("oD1", "nE1"): 0.93})
        moved = align_document(old_doc, new_doc, records, tfidf, tau_moved=0.72)
        moved += block_continuation_pass(old_doc, new_doc, records)
        moves = embed_move_pass(old_doc, new_doc, records, embed, tau_cross=0.88)
        return json.dumps([records, moved, moves], ensure_ascii=False)

    first = _run()
    assert first == _run()
    # and the run really exercised all three passes
    assert '"via": "tfidf"' in first and '"via": "block"' in first and '"via": "embed"' in first
