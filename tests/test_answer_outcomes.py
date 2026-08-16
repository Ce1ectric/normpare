"""Tests for the outcome of a single interpretation answer (AP-18).

Two runs (``out/4110_final``, ``out/4120_final``) lost 18 chapters between them, 13 of
them silently: the answer hit ``max_tokens``, the truncated JSON did not parse, and the
chapter fell back to the extractive summary without a word on the console. The defect is
not the limit but the silence, so these tests pin three things down:

* the *reason* a chapter has no interpretation -- ``ok``, ``truncated``,
  ``api_error:<type>`` or ``unparsable``, with the API's own error type kept;
* the summary at the end of the stage, on the console and in ``pipeline_feedback``,
  which is printed whether anything was lost or not;
* that the answer cache does not key on ``max_tokens``, so raising the limit re-asks the
  failed chapters only.

A fake Anthropic client stands in for the SDK; no test here touches the network.
"""
from __future__ import annotations

import inspect
import io
import json
import token
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from normpare.stages.deutung import (
    MAX_ANSWER_TOKENS,
    LlmClient,
    run_deutung,
)

TRUNCATED_ANSWER = '{"section_id": "1", "interpretations": [{"change": "abgeschnitten'


# -- fakes --------------------------------------------------------------------------------

class _FakeBatches:
    """``client.messages.batches`` with a per-answer ``stop_reason``."""

    def __init__(self, answers: list[tuple[str, str]]):
        self.answers = answers                # (text, stop_reason)
        self.submitted: list | None = None

    def create(self, requests):
        self.submitted = requests
        return SimpleNamespace(id="batch_test")

    def retrieve(self, _bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, _bid):
        for i, (txt, stop) in enumerate(self.answers):
            block = SimpleNamespace(type="text", text=txt)
            message = SimpleNamespace(content=[block], stop_reason=stop)
            yield SimpleNamespace(
                custom_id=f"c{i}",
                result=SimpleNamespace(type="succeeded", message=message))


class _ErroredBatches(_FakeBatches):
    """A batch whose single request the API rejected."""

    def __init__(self, error_type: str):
        super().__init__([])
        self.error_type = error_type

    def results(self, _bid):
        yield SimpleNamespace(
            custom_id="c0",
            result=SimpleNamespace(type="errored",
                                   error=SimpleNamespace(type=self.error_type)))


class _FakeAnthropic:
    def __init__(self, batches):
        self.batches = batches
        self.messages = SimpleNamespace(batches=batches)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    c = LlmClient(tmp_path, "test-model", tmp_path / "cache", tmp_path / "prompts")
    c.live = True                              # no key needed; a fake is injected below
    c.provider = "anthropic"
    return c


# -- a miniature comparison over three chapters -------------------------------------------

def _docs() -> tuple[dict, dict]:
    def doc(interval: str) -> dict:
        return {"sections": [
            {"id": str(i), "title": f"Kapitel {i}", "tables": [], "figures": [],
             "paragraphs": [{"id": f"{i}.p1", "n0": f"Die Anlage ist {interval} zu prüfen.",
                             "n1": f"Die Anlage ist {interval} zu prüfen."}]}
            for i in (1, 2, 3)]}

    return doc("jährlich"), doc("halbjährlich")


def _synopse() -> dict:
    return {"pair": "test", "chapters": [{
        "old_id": str(i), "new_id": str(i), "title": f"Kapitel {i}", "part": "hauptteil",
        "mode": "changed", "n_identical": 0, "old_ids": [str(i)], "new_ids": [str(i)],
        "tables_diff": [],
        "changes": [{"kind": "changed",
                     "old_text": "Die Anlage ist jährlich zu prüfen.",
                     "new_text": "Die Anlage ist halbjährlich zu prüfen."}],
    } for i in (1, 2, 3)]}


ANSWER = {
    "section_id": "1", "summary_old": "Jährlich.", "summary_new": "Halbjährlich.",
    "change_overview": "Halbierung.", "training_relevance": "high", "keywords": [],
    "practical_note": "", "interpretations": [],
}


class _StubProvider:
    """A provider that answers some chapters and reports why the others are missing."""

    live = True

    def __init__(self, answers: dict, outcomes: dict):
        self.answers = answers
        self.outcomes = outcomes

    def resolve(self, items):
        return {tag: self.answers.get(tag) for tag, _prompt in items}


def _run(tmp_path, answers: dict, outcomes: dict) -> dict:
    old, new = _docs()
    return run_deutung(_synopse(), old, new, tmp_path, tmp_path / "out", model="test-model",
                       deutung_provider=_StubProvider(answers, outcomes))


# -- 1..3: the reason a chapter has no interpretation --------------------------------------

def test_a_truncated_answer_is_recognised(client, tmp_path):
    """``stop_reason == "max_tokens"`` is truncation, not a parse error.

    Both say "unusable JSON", but only one of them is fixed by a larger budget, and
    calling it ``unparsable`` hides exactly the 13 chapters this package is about.
    """
    client._anth = _FakeAnthropic(_FakeBatches([(TRUNCATED_ANSWER, "max_tokens")]))
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] is None
    assert client.outcomes["t1"] == "truncated"
    # prompt and raw answer stay together -- that is what made the diagnosis possible
    failed = (tmp_path / "prompts" / "t1.FAILED.txt").read_text(encoding="utf-8")
    assert "prompt one" in failed
    assert TRUNCATED_ANSWER in failed


def test_an_unparsable_answer_stays_unparsable(client):
    """A complete but broken answer keeps its own reason -- a bigger budget won't help."""
    client._anth = _FakeAnthropic(_FakeBatches([("kein JSON, nur Text", "end_turn")]))
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] is None
    assert client.outcomes["t1"] == "unparsable"


def test_the_api_error_type_is_kept(client):
    """``overloaded_error`` is retryable, ``invalid_request_error`` is not -- "error" is
    neither, so the API's own type is carried through."""
    client._anth = _FakeAnthropic(_ErroredBatches("overloaded_error"))
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] is None
    assert client.outcomes["t1"] == "api_error:overloaded_error"


def test_the_sequential_path_reports_truncation_too(client, monkeypatch, tmp_path):
    """``ask_json`` (no ``--batch``) carries the same limit and the same reason."""
    monkeypatch.setattr(client, "_complete",
                        lambda user, max_tokens: (TRUNCATED_ANSWER, "max_tokens"))
    assert client.ask_json("prompt one", "t1") is None
    assert client.outcomes["t1"] == "truncated"
    assert (tmp_path / "prompts" / "t1.FAILED.txt").exists()


# -- 4..6: the summary ---------------------------------------------------------------------

def test_the_summary_lists_every_lost_chapter(tmp_path, capsys):
    """Three failures, three reasons, all three chapters named on the console."""
    _run(tmp_path,
         answers={"2": dict(ANSWER, section_id="2")},
         outcomes={"1": "truncated", "2": "ok", "3": "api_error:overloaded_error"})
    printed = capsys.readouterr().out

    assert "Deutung: 3 Kapitel, 1 gedeutet, 2 ohne Deutung" in printed
    assert "truncated" in printed and "api_error" in printed
    assert "overloaded_error" in printed
    lines = [ln for ln in printed.splitlines() if "truncated" in ln or "api_error" in ln]
    assert any("1" in ln for ln in lines) and any("3" in ln for ln in lines)
    assert "llm_prompts" in printed          # where the evidence is


def test_the_summary_appears_even_without_failures(tmp_path, capsys):
    """A clean run says so. A run that only speaks up on failure cannot be trusted when
    it stays quiet."""
    _run(tmp_path,
         answers={t: dict(ANSWER, section_id=t) for t in ("1", "2", "3")},
         outcomes={t: "ok" for t in ("1", "2", "3")})
    printed = capsys.readouterr().out

    assert "Deutung: 3 Kapitel, 3 gedeutet, 0 ohne Deutung" in printed


def test_the_counts_reach_pipeline_feedback(tmp_path):
    """The same numbers machine-readable, phase ``deutung``, so a later run can compare."""
    out = _run(tmp_path,
               answers={"2": dict(ANSWER, section_id="2")},
               outcomes={"1": "truncated", "2": "ok", "3": "api_error:overloaded_error"})
    feedback = [fb for fb in out["pipeline_feedback"] if fb.get("phase") == "deutung"]

    totals = [fb for fb in feedback if fb.get("field") == "answers"]
    assert len(totals) == 1
    assert totals[0]["n_chapters"] == 3
    assert totals[0]["n_interpreted"] == 1
    assert totals[0]["count"] == 2
    assert totals[0]["reasons"] == {"api_error": 1, "truncated": 1}

    lost = {fb["section_id"]: fb["reason"] for fb in feedback if fb.get("field") == "answer"}
    assert lost == {"1": "truncated", "3": "api_error:overloaded_error"}
    # and it survives the round trip into the artefact
    written = json.loads((tmp_path / "out" / "deutung.json").read_text(encoding="utf-8"))
    assert written["pipeline_feedback"] == out["pipeline_feedback"]


# -- 7..8: the limit and the cache ----------------------------------------------------------

def test_the_cache_key_ignores_max_tokens(client):
    """A repeat run with the raised limit hits the cache of the old one.

    Otherwise raising ``max_tokens`` would re-ask all 171 chapters instead of the 8 that
    failed -- the whole run again, at full price.
    """
    client._anth = _FakeAnthropic(_FakeBatches([('{"section_id": "1"}', "end_turn")]))
    first = client.ask_json_batch([("t1", "prompt one")], max_tokens=16000, poll_s=0)

    client._anth = _FakeAnthropic(_FakeBatches([]))   # a resubmission would answer None
    second = client.ask_json_batch([("t1", "prompt one")], max_tokens=48000, poll_s=0)

    assert first["t1"] == second["t1"] == {"section_id": "1"}
    assert client._anth.batches.submitted is None
    assert "max_tokens" not in inspect.signature(
        __import__("normpare.stages.deutung", fromlist=["cache_key"]).cache_key).parameters


def test_max_tokens_is_a_named_constant():
    """The limit is a constant with a reason next to it, not a number in a signature."""
    assert MAX_ANSWER_TOKENS == 48000
    for fn in (LlmClient.ask_json, LlmClient.ask_json_batch):
        assert inspect.signature(fn).parameters["max_tokens"].default is MAX_ANSWER_TOKENS
    # no number literal is the budget any more -- the comment explaining the old 16000 is
    # exactly what the constant is for, so only executable tokens are checked
    source = Path(inspect.getfile(LlmClient)).read_text(encoding="utf-8")
    numbers = [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
               if t.type == token.NUMBER]
    assert "16000" not in numbers
    assert numbers.count("48000") == 1        # only where the constant is defined
