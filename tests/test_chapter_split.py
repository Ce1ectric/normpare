"""Tests for the coverage of the interpretation stage (AP-19).

``build_chapter_prompt`` asked for at most 40 changes per chapter and dropped the rest.
Measured on the two production runs that is 714 of 2506 changes (4110) and 552 of 2065
(4120) that were never interpreted -- in the professionally most important chapters, and
nothing in output, report or deliverable said so.

The cap itself is a reasonable economy; the silence is the defect. These tests pin down:

* that a chapter with more changes than fit into one request is **split** into several,
  with block 1 unchanged down to the character (otherwise every cached chapter would be
  paid for again);
* that the answers of all blocks are merged by ``change_index``, earlier block wins;
* that a second, *lenient* parse rescues an answer with a raw control character -- and
  reports it as ``repaired``, not as ``ok``;
* that the coverage reaches the summary, ``pipeline_feedback`` and the deliverables.

No test here touches the network; a fake Anthropic client stands in for the SDK.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from normpare.report.component_view import build_component_view, render_component_view
from normpare.stages.deutung import (
    LlmClient,
    build_chapter_prompt,
    build_chapter_prompts,
    cache_key,
    run_deutung,
)

MAX_CHANGES = 40           # the cap of build_chapter_prompt, and the block size


# -- material ------------------------------------------------------------------------------

def _changes(n: int) -> list[dict]:
    """``n`` changes whose priority order is deliberately not their index order."""
    out = []
    for i in range(n):
        c: dict = {"kind": "changed",
                   "old_text": f"Der Nachweis ist alle {i} Jahre zu führen.",
                   "new_text": f"Der Nachweis ist alle {i} Monate zu führen."}
        if i % 7 == 0:                     # a value change ranks high, wherever it stands
            c["kennwerte"] = {"changed": [{"old": {"raw": f"{i} Jahre"},
                                           "new": {"raw": f"{i} Monate"}}]}
        if i % 5 == 3:                     # ... and a cosmetic one ranks last
            c["semantic_equal"] = True
        out.append(c)
    return out


def _chapter(n: int, **extra) -> dict:
    return {"old_id": "11.2", "new_id": "11.2", "mapping_id": "11.2+11.2",
            "title": "Nachweisverfahren", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": ["11.2"], "new_ids": ["11.2"],
            "tables_diff": [], "changes": _changes(n), **extra}


def _docs(n: int) -> tuple[dict, dict]:
    def doc(unit: str) -> dict:
        return {"sections": [{
            "id": "11.2", "title": "Nachweisverfahren", "tables": [], "figures": [],
            "paragraphs": [{"id": "11.2.p1",
                            "n0": f"Der Nachweis ist alle {i} {unit} zu führen.",
                            "n1": f"Der Nachweis ist alle {i} {unit} zu führen."}
                           for i in range(n)]}]}

    return doc("Jahre"), doc("Monate")


def _answer(indices, **extra) -> dict:
    return {"section_id": "11.2", "summary_old": "Jährlich.", "summary_new": "Monatlich.",
            "change_overview": "Verkürzte Fristen.", "training_relevance": "high",
            "keywords": [], "practical_note": "",
            "interpretations": [{"change_index": i, "change": f"Deutung {i}",
                                 "evidence": f"Der Nachweis ist alle {i} Monate zu führen.",
                                 "impact": "", "confidence": 0.9} for i in indices],
            **extra}


class _BlockProvider:
    """Answers the prompts of one chapter block by block, in the order they arrive."""

    live = True

    def __init__(self, answers: list[dict | None], outcomes: list[str] | None = None):
        self.answers = answers
        self.tags: list[str] = []
        self.prompts: list[str] = []
        self.outcomes: dict[str, str] = {}
        self._reasons = outcomes or []

    def resolve(self, items):
        out: dict[str, dict | None] = {}
        for i, (tag, prompt) in enumerate(items):
            self.tags.append(tag)
            self.prompts.append(prompt)
            answer = self.answers[i] if i < len(self.answers) else None
            out[tag] = answer
            if i < len(self._reasons):
                self.outcomes[tag] = self._reasons[i]
            else:
                self.outcomes[tag] = "ok" if answer is not None else "unparsable"
        return out


def _run(tmp_path, chapter: dict, answers: list[dict | None],
         outcomes: list[str] | None = None) -> tuple[dict, _BlockProvider]:
    old, new = _docs(3)
    provider = _BlockProvider(answers, outcomes)
    out = run_deutung({"pair": "test", "chapters": [chapter]}, old, new, tmp_path,
                      tmp_path / "out", model="test-model", deutung_provider=provider)
    return out, provider


# -- 1..4: the split -----------------------------------------------------------------------

def test_a_small_chapter_stays_one_request():
    """A chapter that fits asks exactly one question, and asks it as it did before.

    Character-identical, not merely equivalent: the answer cache keys on the prompt, so
    any cosmetic change here would re-buy all 431 interpreted chapters of the two runs.
    """
    ch = _chapter(MAX_CHANGES)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    today, today_sel = build_chapter_prompt(ch, "alt", "neu")

    assert len(blocks) == 1
    assert blocks[0][0] == today
    assert blocks[0][1] == today_sel
    assert "Teil" not in blocks[0][0].split("ÄNDERUNGEN", 1)[1].splitlines()[0]


def test_a_large_chapter_is_split():
    """205 changes become 6 requests, and every change is in exactly one of them."""
    ch = _chapter(205)
    blocks = build_chapter_prompts(ch, "alt", "neu")

    assert len(blocks) == 6
    assert [len(sel) for _p, sel in blocks] == [40, 40, 40, 40, 40, 5]
    selected = [i for _p, sel in blocks for i in sel]
    assert sorted(selected) == list(range(205))          # complete
    assert len(set(selected)) == len(selected)           # and disjoint
    # the header of a later block says which one it is; the reader of the answer needs it
    header = [p.split("ÄNDERUNGEN", 1)[1].splitlines()[0] for p, _sel in blocks]
    assert "Teil 2 von 6" in header[1]
    assert "Teil 6 von 6" in header[5]
    assert "40 von 205" in header[1]


def test_the_first_block_keeps_the_priority_order():
    """Block 1 is the 40 most relevant changes -- the same 40, in the same request.

    The priority (value change +4, modality shift +3, ...) is not touched by the split:
    what was worth asking first stays worth asking first.
    """
    ch = _chapter(205)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    today, today_sel = build_chapter_prompt(ch, "alt", "neu")

    assert blocks[0][1] == today_sel
    assert blocks[0][0] == today
    assert cache_key("m", "s", blocks[0][0]) == cache_key("m", "s", today)
    # ... and the selection is really a selection, not the first 40 by index
    assert today_sel != list(range(MAX_CHANGES))


def test_only_the_first_block_carries_assets():
    """Tables and figures are asked once. Repeating them in every block would interpret
    them n times and leave the merge to sort out the duplicates."""
    ch = _chapter(100, tables_diff=[{"kind": "new", "new": "t1", "caption": "Grenzwerte"}])
    o_secs = {"11.2": {"id": "11.2", "tables": [], "figures": [], "paragraphs": []}}
    n_secs = {"11.2": {"id": "11.2", "figures": [{"caption": "Bild 1 — Kennlinie"}],
                       "tables": [{"id": "t1", "cells": [["U", "10 kV"], ["I", "2 A"]]}],
                       "paragraphs": []}}
    blocks = build_chapter_prompts(ch, "alt", "neu", o_secs, n_secs)

    assert len(blocks) == 3
    assert "TABELLEN & BILDER" in blocks[0][0]
    assert "10 kV" in blocks[0][0]
    assert all("TABELLEN & BILDER" not in p for p, _sel in blocks[1:])
    assert all("Kennlinie" not in p for p, _sel in blocks[1:])


# -- 5..6: merging the answers -------------------------------------------------------------

def test_answers_are_merged_by_change_index(tmp_path):
    """Every block's interpretations end up in the one chapter of the output."""
    ch = _chapter(100)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    answers = [_answer(sel) for _p, sel in blocks]
    out, provider = _run(tmp_path, ch, answers)

    assert len(provider.tags) == 3
    assert len(set(provider.tags)) == 3            # one prompt file per block
    chapter = out["chapters"][0]
    assert chapter["_source"] == "llm"
    got = sorted(d["change_index"] for d in chapter["interpretations"])
    assert got == list(range(100))
    assert sorted(chapter["_changes_interpreted"]) == list(range(100))
    assert out["coverage"]["n_changes"] == 100
    assert out["coverage"]["n_interpreted"] == 100
    assert out["coverage"]["n_split_chapters"] == 1
    assert out["coverage"]["n_extra_requests"] == 2


def test_a_collision_keeps_the_earlier_block(tmp_path):
    """Two blocks claiming the same change: block 1 wins, and the collision is counted.

    Block 1 holds the highest-priority changes; a later block that answers about one of
    them answers about a change it was not shown.
    """
    ch = _chapter(60)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    first = blocks[0][1][0]
    answers = [_answer(blocks[0][1]),
               _answer(blocks[1][1] + [first])]
    out, _provider = _run(tmp_path, ch, answers)

    chapter = out["chapters"][0]
    indices = [d["change_index"] for d in chapter["interpretations"]]
    assert indices.count(first) == 1
    kept = next(d for d in chapter["interpretations"] if d["change_index"] == first)
    assert kept["change"] == f"Deutung {first}"    # both blocks said this, block 1 counts
    assert len(indices) == 60
    assert out["coverage"]["n_collisions"] == 1


# -- 7..8: the lenient second parse ---------------------------------------------------------

class _FakeBatches:
    def __init__(self, answers: list[tuple[str, str]]):
        self.answers = answers
        self.submitted: list | None = None

    def create(self, requests):
        self.submitted = requests
        return SimpleNamespace(id="batch_test")

    def retrieve(self, _bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, _bid):
        for i, (txt, stop) in enumerate(self.answers):
            message = SimpleNamespace(content=[SimpleNamespace(type="text", text=txt)],
                                      stop_reason=stop)
            yield SimpleNamespace(custom_id=f"c{i}",
                                  result=SimpleNamespace(type="succeeded", message=message))


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


#: A complete answer with a raw newline inside a string -- three of the 18 chapters lost
#: in the two production runs look exactly like this (AP-18, question 6.2).
CONTROL_CHAR_ANSWER = '{"section_id": "8.9.2", "change_overview": "Zwei\nZeilen"}'

#: Broken beyond a parser: a missing comma is a different sentence, not a format issue.
BROKEN_ANSWER = '{"section_id": "8.9.2" "change_overview": "Text"}'


def test_a_raw_control_character_is_repaired(client):
    """``strict=False`` reads it, and the run says so: ``repaired``, never ``ok``.

    The distinction has to survive, otherwise how often the model delivers invalid JSON
    disappears from the record.
    """
    client._anth = _FakeAnthropic(_FakeBatches([(CONTROL_CHAR_ANSWER, "end_turn")]))
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] == {"section_id": "8.9.2", "change_overview": "Zwei\nZeilen"}
    assert client.outcomes["t1"] == "repaired"
    assert not (client.export_dir / "t1.FAILED.txt").exists()


def test_a_truly_broken_answer_stays_unparsable(client):
    """No third attempt. Cutting, bracket-balancing or regex patching would invent
    content, and inventing content is what this package is meant to stop."""
    client._anth = _FakeAnthropic(_FakeBatches([(BROKEN_ANSWER, "end_turn")]))
    out = client.ask_json_batch([("t1", "prompt one")], poll_s=0)

    assert out["t1"] is None
    assert client.outcomes["t1"] == "unparsable"
    assert (client.export_dir / "t1.FAILED.txt").exists()


def test_the_sequential_path_repairs_too(client, monkeypatch):
    """``ask_json`` without ``--batch`` makes the same second attempt."""
    monkeypatch.setattr(client, "_complete",
                        lambda user, max_tokens: (CONTROL_CHAR_ANSWER, "end_turn"))
    assert client.ask_json("prompt one", "t1") == {"section_id": "8.9.2",
                                                   "change_overview": "Zwei\nZeilen"}
    assert client.outcomes["t1"] == "repaired"


# -- 9..11: reporting the coverage ----------------------------------------------------------

def test_coverage_reaches_the_summary(tmp_path, capsys):
    """The console says how many changes were interpreted, and names what is missing."""
    ch = _chapter(45)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    out, _provider = _run(tmp_path, ch, [_answer(blocks[0][1]), None],
                          outcomes=["ok", "truncated"])
    printed = capsys.readouterr().out

    assert "Änderungen: 45, davon gedeutet 40 (88,9 %)" in printed
    assert "Kapitel in Teilanfragen: 1 (1 Zusatzanfragen)" in printed
    # a chapter that is only half interpreted is named with both numbers
    assert "11.2" in printed
    assert "5" in printed
    assert out["coverage"]["n_interpreted"] == 40
    assert out["coverage"]["incomplete"] == [
        {"section_id": "11.2", "mapping_id": "11.2+11.2",
         "n_changes": 45, "n_interpreted": 40}]


def test_coverage_reaches_pipeline_feedback(tmp_path):
    """The same numbers machine-readable, phase ``deutung``."""
    ch = _chapter(45)
    blocks = build_chapter_prompts(ch, "alt", "neu")
    out, _provider = _run(tmp_path, ch, [_answer(blocks[0][1]), None],
                          outcomes=["ok", "truncated"])

    entries = [fb for fb in out["pipeline_feedback"]
               if fb.get("phase") == "deutung" and fb.get("field") == "coverage"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["n_changes"] == 45
    assert entry["n_interpreted"] == 40
    assert entry["n_split_chapters"] == 1
    assert entry["n_extra_requests"] == 1
    assert "40" in entry["finding"] and "45" in entry["finding"]
    # ... and it survives into the artefact
    written = json.loads((tmp_path / "out" / "deutung.json").read_text(encoding="utf-8"))
    assert written["coverage"] == out["coverage"]
    assert written["pipeline_feedback"] == out["pipeline_feedback"]


def test_the_deliverables_note_incomplete_coverage(tmp_path):
    """Whoever builds a course from the component view must see that it is incomplete."""
    coverage = {"n_changes": 2506, "n_interpreted": 1792, "n_split_chapters": 10,
                "n_extra_requests": 32, "n_collisions": 0, "n_repaired": 3,
                "incomplete": [{"section_id": "11.2", "mapping_id": "11.2+11.2",
                                "n_changes": 200, "n_interpreted": 40}]}
    text = render_component_view([], "4110 alt<->neu", "2026-08-16", coverage=coverage)
    assert "1792" in text and "2506" in text
    assert "71,5 %" in text
    assert "unvollständig" in text.lower()

    # complete coverage says nothing -- a warning that is always there is not read
    full = render_component_view([], "4110 alt<->neu", "2026-08-16",
                                 coverage={"n_changes": 10, "n_interpreted": 10,
                                           "n_split_chapters": 0, "n_extra_requests": 0,
                                           "n_collisions": 0, "n_repaired": 0,
                                           "incomplete": []})
    assert "unvollständig" not in full.lower()

    # and the note comes out of deutung.json without the caller passing anything
    path = build_component_view({"chapters": []}, {"chapters": [], "coverage": coverage},
                                tmp_path / "Komponenten.md", "4110 alt<->neu",
                                date="2026-08-16")
    assert "71,5 %" in path.read_text(encoding="utf-8")
