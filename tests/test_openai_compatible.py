"""Tests for the OpenAI-compatible provider path (AP-27).

DeepSeek is addressed through ``--provider openai_compatible --base-url
https://api.deepseek.com``, and it thinks by default (effort ``high``). For a structured
extraction task asked roughly 370 times that costs twice: reasoning tokens are billed as
output, and ``temperature`` -- which this pipeline sets to 0 for reproducibility -- is
ignored while thinking is on. The Anthropic path has switched thinking off since AP-18;
these tests pin the same for the OpenAI-compatible body, and they pin that thinking which
happens *anyway* is counted rather than paid for in silence.

The HTTP call is replaced by a stand-in that keeps the body that was sent; no test here
touches the network (``tests/conftest.py`` blocks sockets for the whole suite).
"""
from __future__ import annotations

import json
import urllib.request
from types import SimpleNamespace

import pytest

from normpare.stages.deutung import (
    LlmClient,
    answer_summary,
    cache_key,
    run_deutung,
)

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
ANSWER_JSON = '{"section_id": "1", "interpretations": []}'
TRUNCATED_JSON = '{"section_id": "1", "interpretations": [{"change": "abgeschnitten'


# -- stand-ins for the HTTP call -----------------------------------------------------------

def _completion(content: str, finish_reason: str = "stop",
                reasoning: str | None = None) -> dict:
    """One ``/chat/completions`` response, optionally with a chain of thought."""
    message: dict = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Capture:
    """Stands in for ``urllib.request.urlopen`` and keeps every request body."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.bodies: list[dict] = []
        self.urls: list[str] = []
        self.headers: list[dict] = []

    def __call__(self, req, timeout=None):
        self.bodies.append(json.loads(req.data.decode("utf-8")))
        self.urls.append(req.full_url)
        self.headers.append(dict(req.headers))
        return _FakeResponse(self.payload)


class _FakeMessages:
    """``client.messages`` of the Anthropic SDK; keeps the keyword arguments."""

    def __init__(self):
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=ANSWER_JSON)],
            stop_reason="end_turn")


@pytest.fixture
def client(tmp_path):
    """A client on the OpenAI-compatible path; no key is looked up, one is injected."""
    c = LlmClient(tmp_path, MODEL, tmp_path / "cache", tmp_path / "prompts",
                  provider="openai_compatible", base_url=BASE_URL)
    c.key = "test-key"
    c.live = True
    return c


# -- a miniature comparison, enough for run_deutung to write its summary --------------------

def _docs() -> tuple[dict, dict]:
    def doc(interval: str) -> dict:
        return {"sections": [
            {"id": "1", "title": "Prüfung", "tables": [], "figures": [],
             "paragraphs": [{"id": "1.p1", "n0": f"Die Anlage ist {interval} zu prüfen.",
                             "n1": f"Die Anlage ist {interval} zu prüfen."}]}]}

    return doc("jährlich"), doc("halbjährlich")


def _synopse() -> dict:
    return {"pair": "test", "chapters": [{
        "old_id": "1", "new_id": "1", "title": "Prüfung", "part": "hauptteil",
        "mode": "changed", "n_identical": 0, "old_ids": ["1"], "new_ids": ["1"],
        "tables_diff": [],
        "changes": [{"kind": "changed",
                     "old_text": "Die Anlage ist jährlich zu prüfen.",
                     "new_text": "Die Anlage ist halbjährlich zu prüfen."}],
    }]}


class _ThinkingProvider:
    """A provider that answered, and reports that it was billed for thinking."""

    live = True

    def __init__(self, n_reasoning: int):
        self.n_reasoning = n_reasoning
        self.outcomes = {"1": "ok"}

    def resolve(self, items):
        return {tag: {"section_id": "1", "summary_old": "Jährlich.",
                      "summary_new": "Halbjährlich.", "change_overview": "Halbierung.",
                      "training_relevance": "high", "keywords": [], "practical_note": "",
                      "interpretations": []}
                for tag, _prompt in items}


# -- 1..2: what the two bodies carry --------------------------------------------------------

def test_the_openai_body_disables_thinking(client, monkeypatch):
    """``thinking: {type: disabled}`` travels with every OpenAI-compatible request.

    Without it every DeepSeek request runs in thinking mode: the chain of thought is
    billed as output and ``temperature: 0`` has no effect at all.
    """
    capture = _Capture(_completion(ANSWER_JSON))
    monkeypatch.setattr(urllib.request, "urlopen", capture)

    assert client.ask_json("prompt one", "t1") == json.loads(ANSWER_JSON)

    body = capture.bodies[0]
    assert body["thinking"] == {"type": "disabled"}
    # the rest of the body is what it always was
    assert body["temperature"] == 0
    assert body["model"] == MODEL
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert capture.urls[0] == BASE_URL + "/chat/completions"


def test_the_anthropic_body_is_unchanged(tmp_path):
    """The Anthropic path keeps exactly the call it had: thinking disabled, no temperature.

    AP-27 touches the other branch only. If this drifts, a provider switch would change
    the question and not just the address it is sent to.
    """
    c = LlmClient(tmp_path, "test-model", tmp_path / "cache", tmp_path / "prompts")
    c.key = "test-key"
    c.live = True
    c.provider = "anthropic"
    messages = _FakeMessages()
    c._anth = SimpleNamespace(messages=messages)

    assert c.ask_json("prompt one", "t1") == json.loads(ANSWER_JSON)

    assert messages.kwargs["thinking"] == {"type": "disabled"}
    assert set(messages.kwargs) == {"thinking", "model", "max_tokens", "system", "messages"}
    assert messages.kwargs["messages"] == [{"role": "user", "content": "prompt one"}]


# -- 3..5: the chain of thought -------------------------------------------------------------

def test_reasoning_content_is_counted(client, monkeypatch, tmp_path, capsys):
    """A ``reasoning_content`` with text means the provider thought anyway -- and billed it.

    Counted on the client and named in the summary of the stage: it costs money, and
    nothing else in the output would ever mention it.
    """
    capture = _Capture(_completion(ANSWER_JSON, reasoning="Erst überlege ich …"))
    monkeypatch.setattr(urllib.request, "urlopen", capture)

    client.ask_json("prompt one", "t1")
    assert client.n_reasoning == 1

    old, new = _docs()
    run_deutung(_synopse(), old, new, tmp_path, tmp_path / "out", model=MODEL,
                deutung_provider=_ThinkingProvider(1))
    printed = capsys.readouterr().out
    reported = [ln for ln in printed.splitlines() if "reasoning_content" in ln]
    assert len(reported) == 1
    assert "1" in reported[0]


def test_an_empty_reasoning_content_is_not_counted(client, monkeypatch):
    """An empty field is no chain of thought -- and a missing one is not either."""
    capture = _Capture(_completion(ANSWER_JSON, reasoning=""))
    monkeypatch.setattr(urllib.request, "urlopen", capture)
    client.ask_json("prompt one", "t1")
    assert client.n_reasoning == 0

    monkeypatch.setattr(urllib.request, "urlopen", _Capture(_completion(ANSWER_JSON)))
    client.ask_json("prompt two", "t2")
    assert client.n_reasoning == 0

    # ... and a run without a single thought says nothing about it
    assert not [ln for ln in answer_summary(1, 1, [], n_reasoning=0)
                if "reasoning_content" in ln]


def test_the_answer_is_read_from_content(client, monkeypatch):
    """The interpretation comes from ``content``; ``reasoning_content`` is never parsed.

    The chain of thought may well contain JSON -- a draft, a rejected variant. Reading it
    would put something into the deliverable that the model discarded.
    """
    capture = _Capture(_completion(
        ANSWER_JSON, reasoning='{"section_id": "verworfen", "interpretations": ["Entwurf"]}'))
    monkeypatch.setattr(urllib.request, "urlopen", capture)

    assert client.ask_json("prompt one", "t1") == json.loads(ANSWER_JSON)
    assert client.outcomes["t1"] == "ok"


# -- 6..7: cache key and truncation ---------------------------------------------------------

def test_the_cache_key_contains_the_model(tmp_path):
    """Another model, another key: answers of different models must not mix.

    A provider switch therefore invalidates the cache, which is the point -- the same
    prompt asked of DeepSeek and of Claude are two different answers.
    """
    system, user = "system prompt", "user prompt"
    assert cache_key(MODEL, system, user) != cache_key("claude-sonnet-5", system, user)

    def _client(model: str) -> LlmClient:
        return LlmClient(tmp_path, model, tmp_path / "cache", tmp_path / "prompts",
                         provider="openai_compatible", base_url=BASE_URL)

    assert _client(MODEL)._cache_key(user) != _client("claude-sonnet-5")._cache_key(user)


def test_length_still_maps_to_truncated(client, monkeypatch, tmp_path):
    """``finish_reason: length`` remains truncation, not a parse error (AP-18 stays valid).

    The extra field in the body must not move the one translation this path performs.
    """
    capture = _Capture(_completion(TRUNCATED_JSON, finish_reason="length"))
    monkeypatch.setattr(urllib.request, "urlopen", capture)

    assert client.ask_json("prompt one", "t1") is None
    assert client.outcomes["t1"] == "truncated"
    failed = (tmp_path / "prompts" / "t1.FAILED.txt").read_text(encoding="utf-8")
    assert TRUNCATED_JSON in failed
