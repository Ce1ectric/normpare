"""Test that the pipeline module imports and constructs without heavy dependencies."""
from __future__ import annotations

import pytest

from normpare.config import Config
from normpare.pipeline import STAGES, Pipeline


def test_pipeline_constructs_and_creates_out_dir(tmp_path):
    Pipeline(Config.for_compare("old.docx", "new.docx", tmp_path / "out"))
    assert (tmp_path / "out").is_dir()
    assert STAGES[0] == "ingest" and STAGES[-1] == "report"


def test_run_rejects_unknown_stage(tmp_path):
    cfg = Config.for_compare("old.docx", "new.docx", tmp_path / "out")
    with pytest.raises(ValueError):
        Pipeline(cfg).run("nonsense")
