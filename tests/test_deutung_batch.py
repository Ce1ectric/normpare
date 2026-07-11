"""Tests for the Anthropic message-batch path of the interpretation stage (~50% cheaper).

A fake Anthropic client stands in for the SDK, so these tests need neither the package nor
an API key.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from normpare.stages.deutung import LlmClient


class _FakeBatches:
    """Minimal stand-in for ``client.messages.batches``."""

    def __init__(self, answers: list[str]):
        self.answers = answers
        self.submitted: list | None = None

    def create(self, requests):
        self.submitted = requests
        return SimpleNamespace(id="batch_test")

    def retrieve(self, _bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, _bid):
        for i, txt in enumerate(self.answers):
            block = SimpleNamespace(type="text", text=txt)
            yield SimpleNamespace(
                custom_id=f"c{i}",
                result=SimpleNamespace(type="succeeded",
                                       message=SimpleNamespace(content=[block])))


class _FakeAnthropic:
    def __init__(self, answers: list[str]):
        self.batches = _FakeBatches(answers)
        self.messages = SimpleNamespace(batches=self.batches)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = LlmClient(tmp_path, "test-model", tmp_path / "cache", tmp_path / "prompts")
    c.live = True            # bypass the SDK/key setup; we inject a fake below
    c.provider = "anthropic"
    return c


def test_batch_parses_answers_and_caches(client):
    client._anth = _FakeAnthropic(['{"section_id": "1"}', '```json\n{"section_id": "2"}\n```'])
    out = client.ask_json_batch([("t1", "prompt one"), ("t2", "prompt two")], poll_s=0)

    assert out["t1"] == {"section_id": "1"}
    assert out["t2"] == {"section_id": "2"}          # code fences are stripped
    assert len(client._anth.batches.submitted) == 2
    # custom_ids must satisfy the API's [a-zA-Z0-9_-] rule
    assert all(r["custom_id"].isalnum() for r in client._anth.batches.submitted)
    # thinking is disabled in batch requests too
    assert client._anth.batches.submitted[0]["params"]["thinking"] == {"type": "disabled"}


def test_batch_serves_cached_prompts_without_resubmitting(client):
    client._anth = _FakeAnthropic(['{"section_id": "1"}'])
    client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    # a fresh fake with no answers: if the client submitted again, the result would be None
    client._anth = _FakeAnthropic([])
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)
    assert out["t1"] == {"section_id": "1"}
    assert client._anth.batches.submitted is None   # nothing was submitted


def test_batch_unparsable_answer_yields_none_and_exports(client, tmp_path):
    client._anth = _FakeAnthropic(["kein JSON, nur Text"])
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] is None                        # -> extractive fallback upstream
    assert (tmp_path / "prompts" / "t1.FAILED.txt").exists()


def test_batch_errored_request_yields_none(client):
    class _Err(_FakeBatches):
        def results(self, _bid):
            yield SimpleNamespace(
                custom_id="c0",
                result=SimpleNamespace(type="errored",
                                       error=SimpleNamespace(type="invalid_request_error")))

    fake = _FakeAnthropic([])
    fake.batches = _Err([])
    fake.messages = SimpleNamespace(batches=fake.batches)
    client._anth = fake
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)
    assert out["t1"] is None


def test_non_anthropic_provider_falls_back_to_sequential(client, monkeypatch):
    client.provider = "openai"
    calls = []

    def fake_ask(user, tag, max_tokens=16000):
        calls.append(tag)
        return {"section_id": tag}

    monkeypatch.setattr(client, "ask_json", fake_ask)
    out = client.ask_json_batch([("t1", "p1"), ("t2", "p2")], poll_s=0)
    assert calls == ["t1", "t2"]
    assert out["t1"] == {"section_id": "t1"}
