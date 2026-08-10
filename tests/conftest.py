"""Suite-wide invariants: no test may open a network connection.

Until now network freedom was a property of the individual tests (every LLM call was
faked). That is an agreement, not a guarantee -- a new test could reach the network
without anyone noticing. This module turns the agreement into an enforced invariant:
every outbound socket operation raises :class:`NetworkAccessError` for the whole run.

The block is installed at import time and never lifted. A test that needs a network
call is a broken test, not a slow one.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "tests" / "fixtures" / "synthetic"

#: Key variables ``resolve_key()`` consults. Left in the environment they would turn a
#: unit test into a live call the moment the socket block were relaxed.
KEY_VARS = ("ANTHROPIC_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")


class NetworkAccessError(RuntimeError):
    """Raised when a test tries to open a network connection."""


def _blocked(*args, **kwargs):
    raise NetworkAccessError(
        "network access is blocked in the test suite (tests/conftest.py) -- "
        "a test that needs the network is a broken test, not a slow one")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.socket.sendto = _blocked


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    """Remove every key variable, so no test can accidentally run against a live API."""
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


# -- the synthetic corpus TR-X 1000 -------------------------------------------------------

def _load_tool(name: str):
    """Import a module from ``tools/`` (not a package, so it is loaded by path)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def expectation() -> dict:
    """``erwartung.toml`` -- the specification of what the corpus must yield.

    Read only. If the pipeline disagrees with it, the implementation is what changes.
    """
    return tomllib.loads((SYNTHETIC / "erwartung.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def synthetic_run(tmp_path_factory) -> SimpleNamespace:
    """Build ``alt.docx``/``neu.docx`` from the Markdown and run the deterministic stages.

    Interpretation is not part of it -- that stage needs an LLM and is covered by the
    fixture provider instead. Everything lands in a temporary directory; the corpus in
    ``tests/fixtures/synthetic/`` is only ever read.
    """
    from normpare.config import Config
    from normpare.pipeline import Pipeline

    build = _load_tool("build_synthetic")
    work = tmp_path_factory.mktemp("synthetic")
    old_docx = build.build_docx(SYNTHETIC / "alt.md", work / "alt.docx")
    new_docx = build.build_docx(SYNTHETIC / "neu.md", work / "neu.docx")
    out = work / "run"
    cfg = Config.for_compare(old_docx, new_docx, out, run_name="synthetic",
                             pair_label="TR-X 1000 2020-01 <-> 2026-01")
    Pipeline(cfg).run(["ingest", "enrich", "map", "align", "synopse", "keywords"])

    def _read(rel: str) -> dict:
        return json.loads((out / rel).read_text(encoding="utf-8"))

    return SimpleNamespace(
        out=out, old_docx=old_docx, new_docx=new_docx,
        mapping=_read("mapping.json"), synopse=_read("synopse.json"),
        old_doc=_read("alt/norm_doc.json"), new_doc=_read("neu/norm_doc.json"),
    )
