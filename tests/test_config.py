"""Tests for the configuration model."""
from __future__ import annotations

import json

from normpare.config import Config


def test_for_compare_derives_metadata():
    cfg = Config.for_compare("/x/old_2019.pdf", "/x/new_2026.docx", "out/demo",
                             provider="openai", model="gpt-4o", language="de")
    assert cfg.old.doc_id == "old_2019" and cfg.old.path == "/x/old_2019.pdf"
    assert cfg.new.doc_id == "new_2026"
    assert cfg.run_name == "demo"
    assert cfg.llm.provider == "openai" and cfg.llm.model == "gpt-4o"
    assert "old_2019" in cfg.pair_label and "new_2026" in cfg.pair_label


def test_for_compare_defaults_model():
    cfg = Config.for_compare("a.pdf", "b.docx", "out")
    assert cfg.llm.model  # a non-empty default model even when none is given


def test_from_file_reads_v2_config_layout(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "run_name": "demo", "pair_label": "P",
        "old": {"path": "a.pdf", "doc_id": "o", "title": "Old", "version_label": "2023"},
        "new": {"path": "b.docx", "doc_id": "n", "title": "New", "version_label": "2026"},
        "similarity": "tfidf", "tau": 0.6,
        "llm_provider": "ollama", "llm_model": "llama3.1", "deutung_scope": "core",
    }), encoding="utf-8")
    cfg = Config.from_file(p, tmp_path / "out")
    assert cfg.run_name == "demo" and cfg.old.title == "Old"
    assert cfg.llm.provider == "ollama" and cfg.llm.scope == "core"
    assert cfg.tau == 0.6
