"""Tests for the run manifest (ENT-29).

Every run writes ``manifest.json`` next to its artifacts: version, timestamp, the input
files with their SHA-256 and every effective parameter. The single most important field
is ``schema_version`` -- its absence is what made the half-migrated state of
``out/4110_hot`` invisible (ENT-25).

The manifest carries a timestamp and is therefore excluded from the byte comparison of
the regression harness, like the ``.docx`` and ``.html`` artifacts.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from normpare.config import Config
from normpare.manifest import SCHEMA_VERSION, build_manifest, write_manifest

_SPEC = importlib.util.spec_from_file_location(
    "regression", Path(__file__).resolve().parents[1] / "tools" / "regression.py")
regression = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(regression)


def _config(tmp_path) -> Config:
    old = tmp_path / "alt.docx"
    new = tmp_path / "neu.docx"
    old.write_bytes(b"old bytes")
    new.write_bytes(b"new bytes")
    return Config.for_compare(old, new, tmp_path / "out", language="de", model="test-model")


def test_manifest_records_schema_version_and_inputs(tmp_path):
    """Version, timestamp and both inputs with their hash."""
    cfg = _config(tmp_path)
    man = build_manifest(cfg)

    assert man["schema_version"] == SCHEMA_VERSION == 2
    assert man["normpare_version"]
    assert man["created"].endswith("Z")
    assert man["inputs"]["old"]["sha256"] == hashlib.sha256(b"old bytes").hexdigest()
    assert man["inputs"]["new"]["bytes"] == len(b"new bytes")
    assert man["inputs"]["old"]["path"].endswith("alt.docx")


def test_manifest_survives_a_run_without_source_files(tmp_path):
    """A replay has no source files -- it starts from the frozen ``norm_doc.json``.

    ``SourceSpec.path`` is empty there, and an empty path is the current directory. The
    manifest must record "no input file" instead of trying to hash a directory, or the
    replay driver cannot run at all.
    """
    from normpare.config import Config as _Config
    from normpare.config import SourceSpec

    cfg = _Config(old=SourceSpec("", "old", "old", "old"),
                  new=SourceSpec("", "new", "new", "new"),
                  out_dir=str(tmp_path / "out"), run_name="replay", pair_label="old <-> new")
    inputs = build_manifest(cfg, stages=["map"], use_llm=False)["inputs"]
    assert inputs["old"]["sha256"] is None and inputs["old"]["bytes"] is None
    assert inputs["new"]["sha256"] is None and inputs["new"]["bytes"] is None


def test_manifest_records_every_effective_parameter(tmp_path):
    """Anything that steers the result is in the manifest -- a baseline must be
    self-explanatory without guessing the run parameters."""
    cfg = _config(tmp_path)
    params = build_manifest(cfg)["parameters"]

    assert params["tau"] == cfg.tau and params["tau_moved"] == cfg.tau_moved
    assert params["similarity"] == "tfidf" and params["language"] == "de"
    assert params["embed_fallback"] is False and params["tau_embed"] == cfg.tau_embed
    assert params["llm_provider"] == "anthropic" and params["llm_model"] == "test-model"
    assert params["llm_scope"] == "core" and params["llm_batch"] is False


def test_write_manifest_lands_next_to_the_artifacts(tmp_path):
    cfg = _config(tmp_path)
    path = write_manifest(cfg, tmp_path / "out", stages=["ingest"], use_llm=False)

    assert path == tmp_path / "out" / "manifest.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["stages"] == ["ingest"] and written["use_llm"] is False


def test_manifest_is_not_byte_compared(tmp_path):
    """It contains a timestamp, so it can never be byte-identical between two runs."""
    assert "manifest.json" not in regression.ARTIFACTS
    assert "manifest.json" in regression.NOT_COMPARED
