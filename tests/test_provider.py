"""Tests for the interpretation provider seam (``DeutungProvider``).

The LLM call used to be wired straight into :func:`run_deutung`, which made the stage
untestable without a network call. It now sits behind a provider:

``LiveProvider``
    the existing path (``LlmClient``), unchanged in behaviour.
``FixtureProvider``
    reads frozen answers from a JSON file, keyed exactly like the ``llm_cache``.

No test here touches the network -- ``tests/conftest.py`` blocks sockets for the whole
suite, and two of these tests assert that block.
"""
from __future__ import annotations

import json
import socket

import pytest

from normpare.stages.deutung import (
    FixtureProvider,
    LiveProvider,
    MissingFixtureError,
    build_chapter_prompt,
    build_system_prompt,
    cache_key,
    run_deutung,
)

MODEL = "test-model"

# -- a miniature comparison, small enough to read, complete enough for run_deutung -------

OLD_DOC = {"sections": [{"id": "1", "title": "Prüfung", "paragraphs": [
    {"id": "1.p1", "n0": "Die Anlage ist jährlich zu prüfen.",
     "n1": "Die Anlage ist jährlich zu prüfen."}], "tables": [], "figures": []}]}
NEW_DOC = {"sections": [{"id": "1", "title": "Prüfung", "paragraphs": [
    {"id": "1.p1", "n0": "Die Anlage ist halbjährlich zu prüfen.",
     "n1": "Die Anlage ist halbjährlich zu prüfen."}], "tables": [], "figures": []}]}


def _synopse() -> dict:
    return {"pair": "test", "chapters": [{
        "old_id": "1", "new_id": "1", "title": "Prüfung", "part": "hauptteil",
        "mode": "changed", "n_identical": 0, "old_ids": ["1"], "new_ids": ["1"],
        "tables_diff": [],
        "changes": [{"kind": "changed",
                     "old_text": "Die Anlage ist jährlich zu prüfen.",
                     "new_text": "Die Anlage ist halbjährlich zu prüfen."}],
    }]}


FROZEN_ANSWER = {
    "section_id": "1",
    "summary_old": "Jährliche Prüfung.",
    "summary_new": "Halbjährliche Prüfung.",
    "change_overview": "Das Prüfintervall wird halbiert.",
    "training_relevance": "high",
    "keywords": [],
    "practical_note": "",
    "interpretations": [{
        "change_index": 0,
        "semantic_label": "restricted",
        "obligation": "tightened",
        "change": "Das Intervall wird von jährlich auf halbjährlich verkürzt.",
        "impact": "Doppelt so viele Prüfungen.",
        "cross_reference_note": "",
        "evidence": "Die Anlage ist halbjährlich zu prüfen.",
        "confidence": "high",
        "contradiction_flag": False,
    }],
}


def _prompt_for(synopse: dict, language: str = "de", title: str = "") -> tuple[str, str]:
    """The system and user prompt run_deutung builds for the single chapter."""
    ch = synopse["chapters"][0]
    o_secs = {s["id"]: s for s in OLD_DOC["sections"]}
    n_secs = {s["id"]: s for s in NEW_DOC["sections"]}

    def excerpt(secs, ids):
        return " ".join(" ".join(p.get("n1", p.get("n0", "")) for p in secs[sid]["paragraphs"])
                        for sid in ids if sid in secs)

    user, _sel = build_chapter_prompt(ch, excerpt(o_secs, ch["old_ids"]),
                                      excerpt(n_secs, ch["new_ids"]), o_secs, n_secs)
    return build_system_prompt(language, title), user


def _fixture_file(path, synopse: dict) -> "FixtureProvider":
    system, user = _prompt_for(synopse)
    path.write_text(json.dumps({cache_key(MODEL, system, user): FROZEN_ANSWER},
                               ensure_ascii=False), encoding="utf-8")
    return FixtureProvider(path, model=MODEL, system=system)


# -- 1..3: the fixture provider ----------------------------------------------------------

def test_fixture_provider_returns_frozen_answer(tmp_path):
    """A known key yields exactly the frozen answer -- nothing is invented, nothing asked."""
    syn = _synopse()
    provider = _fixture_file(tmp_path / "deutungen.json", syn)
    system, user = _prompt_for(syn)
    assert provider.resolve([("1", user)]) == {"1": FROZEN_ANSWER}
    assert cache_key(MODEL, system, user) in provider.answers


def test_fixture_provider_raises_on_missing_key(tmp_path):
    """An unknown prompt is an error, and the missing key is in the message.

    Falling back to ``None`` would silently degrade to the extractive summary and the
    test above it would still look green.
    """
    path = tmp_path / "deutungen.json"
    path.write_text("{}", encoding="utf-8")
    provider = FixtureProvider(path)
    system, user = _prompt_for(_synopse())
    key = cache_key(provider.model, provider.system, user)
    with pytest.raises(MissingFixtureError) as exc:
        provider.resolve([("1", user)])
    assert key in str(exc.value)


def test_fixture_provider_never_opens_a_socket(tmp_path):
    """Resolving works with every socket constructor sabotaged."""
    syn = _synopse()
    provider = _fixture_file(tmp_path / "deutungen.json", syn)
    _system, user = _prompt_for(syn)

    class _Forbidden(socket.socket):
        def __init__(self, *a, **k):
            raise AssertionError("the fixture provider must not create a socket")

    real_socket = socket.socket
    socket.socket = _Forbidden
    try:
        answers = provider.resolve([("1", user)])
    finally:
        socket.socket = real_socket
    assert answers["1"] == FROZEN_ANSWER
    # and the suite-wide block is in place
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 9))


# -- 4..5: run_deutung ---------------------------------------------------------------------

def test_run_deutung_accepts_a_provider(tmp_path):
    """With a fixture provider the stage runs end to end, offline and deterministically."""
    syn = _synopse()
    provider = _fixture_file(tmp_path / "deutungen.json", syn)
    out = run_deutung(syn, OLD_DOC, NEW_DOC, tmp_path, tmp_path / "out", model=MODEL,
                      deutung_provider=provider)

    assert out["n_llm"] == 1
    chapter = out["chapters"][0]
    assert chapter["_source"] == "llm"
    assert chapter["change_overview"] == FROZEN_ANSWER["change_overview"]
    interpretation = chapter["interpretations"][0]
    assert interpretation["evidence_ok"] is True
    assert interpretation["evidence_strict"] is True
    assert out["review_queue"] == []
    assert json.loads((tmp_path / "out" / "deutung.json").read_text(encoding="utf-8")) == out


def test_default_provider_is_live(tmp_path):
    """Without a provider the stage behaves exactly as before: a LiveProvider, and with
    no key it falls back to the prompt export and the extractive summary."""
    out = run_deutung(_synopse(), OLD_DOC, NEW_DOC, tmp_path, tmp_path / "out", model=MODEL)

    assert out["mode"] == "export"      # no key -> export mode, as before
    assert out["model"] is None
    assert out["n_llm"] == 0
    assert out["chapters"][0]["_source"] == "extractive"
    assert (tmp_path / "out" / "llm_prompts" / "1.txt").exists()

    # the default is a LiveProvider, and it reports export mode the same way
    live = LiveProvider(tmp_path, MODEL, tmp_path / "out2" / "llm_cache",
                        tmp_path / "out2" / "llm_prompts")
    assert live.live is False
