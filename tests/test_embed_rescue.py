"""Tests for ADR-0001: the optional embedding rescue pass and the vector cache.

The cascade logic is exercised with a fake backend (a fixed similarity matrix), so these
tests need neither ``sentence-transformers`` nor model access.
"""
from __future__ import annotations

import numpy as np
import pytest

from normpare.stages.align import paras as paras_mod
from normpare.stages.align.paras import embed_rescue_pass
from normpare.text.similarity import VectorCache


def _greedy_lsa(cost):
    """Deterministic greedy assignment (SciPy-free) used to run these tests in an
    environment without a working SciPy; optimal for the tiny matrices used here."""
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


class FakeBackend:
    """Similarity by chapter tag: paragraphs of the same chapter score high, else low.

    The chapter tag is the second whitespace token of the text (see ``_long``), so the
    fake needs no fixed matrix shape and works per chapter.
    """

    def __init__(self, high=0.90, low=0.10):
        self.high, self.low = high, low

    def sim_matrix(self, a, b):
        key = lambda t: t.split()[1]
        return np.array([[self.high if key(x) == key(y) else self.low for y in b] for x in a],
                        dtype=float)


def _long(tag: str) -> str:
    return f"alt {tag} " + "wort " * 12  # 'alt'/'neu' irrelevant; token[1] is the chapter tag


def _fixture():
    old_doc = {"sections": [
        {"id": "A", "paragraphs": [{"id": "oA1", "n1": _long("A")}]},
        {"id": "C", "paragraphs": [{"id": "oC1", "n1": _long("C")}]},
    ]}
    new_doc = {"sections": [
        {"id": "A", "paragraphs": [{"id": "nA1", "n1": _long("A")}]},
        {"id": "B", "paragraphs": [{"id": "nB1", "n1": _long("B")}]},
    ]}
    records = [
        {"chapter": "A", "para_links": [
            {"old_ids": ["oA1"], "new_ids": [], "kind": "removed", "confidence": 0.0},
            {"old_ids": [], "new_ids": ["nA1"], "kind": "new", "confidence": 0.0},
        ]},
        {"chapter": "B", "para_links": [
            {"old_ids": [], "new_ids": ["nB1"], "kind": "new", "confidence": 0.0},
        ]},
        {"chapter": "C", "para_links": [
            {"old_ids": ["oC1"], "new_ids": [], "kind": "removed", "confidence": 0.0},
        ]},
    ]
    return old_doc, new_doc, records


def test_same_chapter_pair_is_fused_to_similar():
    old_doc, new_doc, records = _fixture()
    rescued = embed_rescue_pass(old_doc, new_doc, records, FakeBackend(), tau_embed=0.78)

    links_a = records[0]["para_links"]
    assert len(links_a) == 1                       # the 'new' link was fused away
    assert links_a[0]["kind"] == "similar"
    assert links_a[0]["old_ids"] == ["oA1"] and links_a[0]["new_ids"] == ["nA1"]
    assert links_a[0]["via"] == "embed"
    assert len(rescued) == 1 and rescued[0]["same_chapter"] is True


def test_cross_chapter_residuals_are_not_rescued():
    # a 'removed' in chapter C and a 'new' in chapter B must stay as they are:
    # cross-chapter moves are left to the deterministic pass, not the embedding rescue.
    old_doc, new_doc, records = _fixture()
    rescued = embed_rescue_pass(old_doc, new_doc, records, FakeBackend(), tau_embed=0.78)

    assert all(r["same_chapter"] for r in rescued)
    assert records[1]["para_links"][0]["kind"] == "new"
    assert records[2]["para_links"][0]["kind"] == "removed"


def test_below_threshold_leaves_records_untouched():
    old_doc, new_doc, records = _fixture()
    # even the same-chapter score (0.90) is below this threshold -> nothing rescued
    rescued = embed_rescue_pass(old_doc, new_doc, records, FakeBackend(), tau_embed=0.95)

    assert rescued == []
    assert [l["kind"] for l in records[0]["para_links"]] == ["removed", "new"]
    assert records[1]["para_links"][0]["kind"] == "new"
    assert records[2]["para_links"][0]["kind"] == "removed"


def test_short_fragments_are_ignored():
    old_doc = {"sections": [{"id": "A", "paragraphs": [{"id": "o", "n1": "kurz"}]}]}
    new_doc = {"sections": [{"id": "A", "paragraphs": [{"id": "n", "n1": "kurz"}]}]}
    records = [{"chapter": "A", "para_links": [
        {"old_ids": ["o"], "new_ids": [], "kind": "removed", "confidence": 0.0},
        {"old_ids": [], "new_ids": ["n"], "kind": "new", "confidence": 0.0},
    ]}]
    rescued = embed_rescue_pass(old_doc, new_doc, records, FakeBackend(), tau_embed=0.78)
    assert rescued == []  # both below min_len -> nothing to rescue


def test_vector_cache_roundtrip(tmp_path):
    path = tmp_path / "vec.npz"
    cache = VectorCache(path, "model-x")
    assert cache.get("hallo welt") is None
    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    cache.put("hallo welt", vec)
    np.testing.assert_allclose(cache.get("hallo welt"), vec)
    cache.flush()

    reloaded = VectorCache(path, "model-x")
    np.testing.assert_allclose(reloaded.get("hallo welt"), vec)
    # a different model name must not collide
    assert VectorCache(path, "model-y").get("hallo welt") is None
