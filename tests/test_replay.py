"""Tests for the replay driver (``tools/replay.py``) -- AP-32.

The replay is the measuring instrument of the project: every change since AP-01 is
held against a baseline through it. It therefore has to start where it claims to
start. ``Pipeline.map`` writes the titles of absorbed old sections back into
``alt/norm_doc.json`` as synthetic paragraphs, so a finished run carries them; if the
replay recomputed over them, they would take part in the similarity computation a
second time, a different set of sections would be absorbed, and the run would not be
reproduced but continued.

Two miniature stages of proof: the copy step on hand-written documents, and the whole
replay on the synthetic corpus TR-X 1000 (session fixture ``synthetic_run``). No test
reads ``out/``, none touches the network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str):
    """Import a module from ``tools/`` -- the instance ``replay`` itself imports.

    ``tools/`` is not a package, so every test module loading it by path would get an
    instance of its own; ``replay.py`` does ``import regression`` and would then raise
    an exception class that is not the one this module knows.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


regression = _tool("regression")
replay = _tool("replay")


# -- a miniature reference run ----------------------------------------------------------

def _para(pid: str, text: str, synthetic: bool = False) -> dict:
    para = {"id": pid, "n0": text, "n1": text.lower(), "kind": "text",
            "modality": "informativ", "refs_internal": [], "values": []}
    if synthetic:
        para["synthetic_title"] = True
    return para


def _doc(doc_id: str, synthetic: bool) -> dict:
    """A document whose section 2 carries a synthetic title paragraph if asked for."""
    return {
        "doc_id": doc_id,
        "sections": [
            {"id": "1", "title": "Scope", "level": 1, "part": "main",
             "paragraphs": [_para("1.p1", "first"), _para("1.p2", "second")],
             "tables": [], "figures": []},
            {"id": "2", "title": "Terms", "level": 1, "part": "main",
             "paragraphs": ([_para("2.p0", "Terms", synthetic=True)] if synthetic else [])
                           + [_para("2.p1", "third")],
             "tables": [], "figures": []},
        ],
    }


def make_reference(ref: Path, *, alt_synthetic: bool = True,
                   neu_synthetic: bool = False) -> Path:
    """Write a reference run that carries the artifacts ``stage_inputs`` copies."""
    for side, synthetic in (("alt", alt_synthetic), ("neu", neu_synthetic)):
        p = ref / side / "norm_doc.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_doc(side, synthetic), ensure_ascii=False, indent=1),
                     encoding="utf-8")
    (ref / "mapping.json").write_text(
        json.dumps({"pair": "old <-> new", "records": []}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return ref


def _paragraphs(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [p for s in doc["sections"] for p in s.get("paragraphs") or []]


def _synthetic_ids(path: Path) -> list[str]:
    return [p["id"] for p in _paragraphs(path) if p.get("synthetic_title")]


def _tree_digests(root: Path) -> dict[str, str]:
    """SHA-256 of every file below ``root``, keyed by relative path."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# -- the copy step ----------------------------------------------------------------------

def test_synthetic_paragraphs_are_stripped_before_replay(tmp_path):
    """What ``map`` inserted is gone again before ``map`` runs a second time."""
    ref = make_reference(tmp_path / "ref")
    assert _synthetic_ids(ref / "alt" / "norm_doc.json") == ["2.p0"]

    replay.stage_inputs(ref, tmp_path / "dest")

    assert _synthetic_ids(tmp_path / "dest" / "alt" / "norm_doc.json") == []


def test_only_synthetic_paragraphs_are_stripped(tmp_path):
    """Every other paragraph survives unchanged and in the same order."""
    ref = make_reference(tmp_path / "ref")
    expected = [p for p in _paragraphs(ref / "alt" / "norm_doc.json")
                if not p.get("synthetic_title")]

    replay.stage_inputs(ref, tmp_path / "dest")

    assert _paragraphs(tmp_path / "dest" / "alt" / "norm_doc.json") == expected
    # the sections themselves are untouched -- an emptied section stays in the document
    reference = json.loads((ref / "alt" / "norm_doc.json").read_text(encoding="utf-8"))
    copied = json.loads((tmp_path / "dest" / "alt" / "norm_doc.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in copied["sections"]] == [s["id"] for s in reference["sections"]]


def test_a_document_without_synthetic_paragraphs_is_untouched(tmp_path):
    """Nothing to remove means nothing to rewrite -- the copy stays byte for byte."""
    ref = make_reference(tmp_path / "ref", alt_synthetic=False)

    replay.stage_inputs(ref, tmp_path / "dest")

    for side in ("alt", "neu"):
        assert ((tmp_path / "dest" / side / "norm_doc.json").read_bytes()
                == (ref / side / "norm_doc.json").read_bytes())


def test_the_new_side_is_stripped_too(tmp_path):
    """Today the new side carries none in any reference run -- the handling must not
    depend on that: ``map`` absorbs on both sides, and only the old one materializes
    titles today."""
    ref = make_reference(tmp_path / "ref", alt_synthetic=False, neu_synthetic=True)
    assert _synthetic_ids(ref / "neu" / "norm_doc.json") == ["2.p0"]

    replay.stage_inputs(ref, tmp_path / "dest")

    assert _synthetic_ids(tmp_path / "dest" / "neu" / "norm_doc.json") == []


# -- the whole replay, on the synthetic corpus -------------------------------------------

@pytest.fixture(scope="session")
def replayed(synthetic_run, tmp_path_factory) -> SimpleNamespace:
    """Replay the synthetic run, then replay that replay. Built once, read by three tests."""
    work = tmp_path_factory.mktemp("replay")
    before = _tree_digests(synthetic_run.out)
    first = replay.replay(synthetic_run.out, work / "replay1")
    second = replay.replay(first, work / "replay2")
    return SimpleNamespace(reference=synthetic_run.out, first=first, second=second,
                           before=before, after=_tree_digests(synthetic_run.out))


def test_replaying_a_replay_is_byte_equal(replayed):
    """Idempotence over all eight compared artifacts -- the second pass changes nothing."""
    for name in regression.ARTIFACTS:
        a, b = replayed.first / name, replayed.second / name
        assert a.exists() and b.exists(), f"{name} missing from a replay"
        assert a.read_bytes() == b.read_bytes(), f"{name} differs between two replays"


def test_the_stripped_paragraphs_are_recreated_by_map(replayed):
    """Removing them loses nothing: ``map`` inserts them again, with the same ids."""
    original = _synthetic_ids(replayed.reference / "alt" / "norm_doc.json")
    assert original, "the synthetic corpus must produce synthetic title paragraphs"
    assert _synthetic_ids(replayed.first / "alt" / "norm_doc.json") == original


def test_the_frozen_files_are_not_modified(replayed, tmp_path):
    """The reference is read, never written -- and ``guard_write`` still refuses ``out/``."""
    assert replayed.after == replayed.before

    ref = make_reference(tmp_path / "ref")
    with pytest.raises(regression.WriteToOutError):
        replay.stage_inputs(ref, tmp_path / "out" / "dest")


def test_the_docstring_names_the_post_map_state():
    """The module says what it actually gets: a run after ``map``, not after ``enrich``."""
    doc = replay.__doc__
    assert "already enriched" not in doc, "the claim that no longer holds is still there"
    assert "map" in doc
    assert "synthetic_title" in doc
