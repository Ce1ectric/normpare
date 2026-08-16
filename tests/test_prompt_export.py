"""Interpretation without an API: export the prompts, play the answers back (AP-21).

The answer cache is keyed on ``model + system prompt + user prompt`` and knows nothing
about the provider, so whoever puts a file under the right key has interpreted the
chapter -- the pipeline will not ask anybody again. Two tools make that usable:

* ``tools/pending_prompts.py`` rebuilds every prompt a run over a finished directory
  would send (split blocks included, AP-19), computes the key and exports only those
  without a cache file. It checks the key recipe against the answers already in the
  cache **before** it exports anything: a wrong model or system prompt would produce
  answers nobody ever finds, and the mistake would surface hours later.
* ``tools/apply_answer.py`` checks an answer and puts it into the cache -- checking, not
  repairing. A wrong answer in the cache is worse than a missing one, because nothing
  ever questions it again.

Every test builds its own miniature run below ``tmp_path``; none reads ``out/``, none
interprets standard text, and no LLM is called.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from normpare.stages.deutung import build_chapter_prompts, build_system_prompt, cache_key

MODEL = "test-model"
TITLE = "TR-X_1000_neu"


@pytest.fixture(scope="module")
def pending_prompts(load_tool):
    return load_tool("pending_prompts")


@pytest.fixture(scope="module")
def apply_answer(load_tool):
    return load_tool("apply_answer")


# -- material ------------------------------------------------------------------------------

def _changes(n: int, chapter: str = "11.2") -> list[dict]:
    """``n`` changes whose priority order is deliberately not their index order."""
    out = []
    for i in range(n):
        c: dict = {"kind": "changed",
                   "old_text": f"In {chapter} ist der Nachweis alle {i} Jahre zu führen.",
                   "new_text": f"In {chapter} ist der Nachweis alle {i} Monate zu führen."}
        if i % 7 == 0:
            c["kennwerte"] = {"changed": [{"old": {"raw": f"{i} Jahre"},
                                           "new": {"raw": f"{i} Monate"}}]}
        out.append(c)
    return out


def _chapter(cid: str, n: int, **extra) -> dict:
    return {"old_id": cid, "new_id": cid, "mapping_id": f"{cid}+{cid}",
            "title": "Nachweisverfahren", "part": "hauptteil", "mode": "changed",
            "n_identical": 0, "old_ids": [cid], "new_ids": [cid],
            "tables_diff": [], "changes": _changes(n, cid), **extra}


def _section(cid: str, unit: str) -> dict:
    return {"id": cid, "title": "Nachweisverfahren", "tables": [], "figures": [],
            "paragraphs": [{"id": f"{cid}.p1",
                            "n0": f"In {cid} ist der Nachweis alle 0 {unit} zu führen.",
                            "n1": f"In {cid} ist der Nachweis alle 0 {unit} zu führen."}]}


def _run_dir(tmp_path: Path, chapters: list[dict], *, model: str = MODEL,
             title: str = TITLE, language: str = "de", name: str = "run") -> Path:
    """A miniature run directory: the four files the prompt building reads."""
    run = tmp_path / name
    (run / "alt").mkdir(parents=True, exist_ok=True)
    (run / "neu").mkdir(parents=True, exist_ok=True)

    def _dump(rel: str, data: dict) -> None:
        (run / rel).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    ids = [ch["new_id"] for ch in chapters]
    _dump("alt/norm_doc.json", {"sections": [_section(c, "Jahre") for c in ids]})
    _dump("neu/norm_doc.json", {"sections": [_section(c, "Monate") for c in ids]})
    _dump("synopse.json", {"pair": "test", "chapters": chapters})
    _dump("manifest.json", {"run_name": name, "pair_label": "alt <-> neu",
                            "inputs": {"old": {"doc_id": "TR-X_1000_alt"},
                                       "new": {"doc_id": title}},
                            "parameters": {"llm_model": model, "language": language,
                                           "llm_scope": "core"}})
    return run


def _excerpt(run: Path, side: str, cid: str) -> str:
    doc = json.loads((run / side / "norm_doc.json").read_text(encoding="utf-8"))
    secs = {s["id"]: s for s in doc["sections"]}
    return " ".join(p.get("n1", p.get("n0", "")) for p in secs[cid]["paragraphs"])


def _prompts(run: Path, ch: dict) -> list[tuple[str, list[int]]]:
    """The blocks of one chapter, built exactly as the stage builds them."""
    old = json.loads((run / "alt" / "norm_doc.json").read_text(encoding="utf-8"))
    new = json.loads((run / "neu" / "norm_doc.json").read_text(encoding="utf-8"))
    o_secs = {s["id"]: s for s in old["sections"]}
    n_secs = {s["id"]: s for s in new["sections"]}
    cid = ch["new_id"]
    return build_chapter_prompts(ch, _excerpt(run, "alt", cid), _excerpt(run, "neu", cid),
                                 o_secs, n_secs)


def _key(prompt: str, model: str = MODEL, title: str = TITLE) -> str:
    return cache_key(model, build_system_prompt("de", title), prompt)


def _cache(run: Path, prompt: str, answer: dict, model: str = MODEL) -> Path:
    """Put an answer into the run's cache, the way the client writes it."""
    cache_dir = run / "llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_key(prompt, model)}.json"
    path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return path


def _answer(indices, chapter: str = "11.2", **extra) -> dict:
    return {"summary_old": "Jährlich.", "summary_new": "Monatlich.",
            "change_overview": "Verkürzte Fristen.", "training_relevance": "high",
            "keywords": [], "practical_note": "",
            "interpretations": [
                {"change_index": i,
                 "semantic_status": "narrowed", "normative_direction": "tightened",
                 "affected_components": ["deadline"], "indeterminate_reason": "",
                 "change": f"Deutung {i}", "impact": "",
                 "evidence": f"In {chapter} ist der Nachweis alle {i} Monate zu führen.",
                 "confidence": "high", "contradiction_flag": False}
                for i in indices],
            **extra}


def _manifest(work: Path) -> dict:
    return json.loads((work / "manifest.json").read_text(encoding="utf-8"))


def _export(pending_prompts, run: Path, work: Path, *extra) -> tuple[int, str]:
    rc = pending_prompts.main(["--dir", str(run), "--work", str(work), *extra])
    return rc, ""


# -- part 1: exporting the open prompts -----------------------------------------------------

def test_cached_prompts_are_skipped(tmp_path, pending_prompts, capsys):
    """A chapter whose answer is already in the cache is not exported again.

    The cache *is* the progress: an interpretation run must never be handed a chapter
    that has been paid for already.
    """
    chapters = [_chapter("8.1", 2), _chapter("9.3", 2)]
    run = _run_dir(tmp_path, chapters)
    cached_prompt = _prompts(run, chapters[0])[0][0]
    _cache(run, cached_prompt, _answer([0, 1], "8.1"))
    work = tmp_path / "work"

    rc = pending_prompts.main(["--dir", str(run), "--work", str(work)])
    text = capsys.readouterr().out

    assert rc == 0
    entries = _manifest(work)["prompts"]
    assert [e["chapter"] for e in entries] == ["9.3"]
    assert sorted(p.name for p in work.glob("*.txt")) == [entries[0]["file"]]
    assert MODEL in text                       # the model in use is always reported
    assert "1" in text and "2" in text         # 2 prompts, 1 cached, 1 open


def test_the_key_check_stops_on_a_mismatch(tmp_path, pending_prompts, capsys):
    """A wrong model aborts the export -- and nothing is written.

    Model and system prompt enter every key alike, so if either is wrong *no* cached
    answer can be found. The export would then produce answers the pipeline never picks
    up, and the mistake would only show after hours of interpretation work.
    """
    chapters = [_chapter("8.1", 2), _chapter("9.3", 2)]
    run = _run_dir(tmp_path, chapters)
    _cache(run, _prompts(run, chapters[0])[0][0], _answer([0, 1], "8.1"))
    work = tmp_path / "work"

    rc = pending_prompts.main(["--dir", str(run), "--work", str(work),
                               "--model", "wrong-model"])
    text = capsys.readouterr().out + capsys.readouterr().err

    assert rc != 0
    assert "wrong-model" in text                        # the model it tried
    assert "system" in text.lower()                     # ... and the other half of the key
    assert not work.exists() or not list(work.glob("*"))


def test_split_chapters_export_every_block(tmp_path, pending_prompts):
    """205 changes become 6 prompts, and the manifest says which block is which."""
    chapters = [_chapter("11.2", 205)]
    run = _run_dir(tmp_path, chapters)
    work = tmp_path / "work"

    assert pending_prompts.main(["--dir", str(run), "--work", str(work)]) == 0

    entries = _manifest(work)["prompts"]
    assert len(entries) == 6
    assert [e["part"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert {e["n_parts"] for e in entries} == {6}
    assert [len(e["change_indices"]) for e in entries] == [40, 40, 40, 40, 40, 5]
    # every change is asked about exactly once
    asked = [i for e in entries for i in e["change_indices"]]
    assert sorted(asked) == list(range(205))
    # ... and the blocks are told apart by their tag, so two answers never collide
    assert len({e["tag"] for e in entries}) == 6


def test_the_manifest_matches_the_files(tmp_path, pending_prompts):
    """One file per entry, and the recorded key is the key of that file's text.

    This is the join between the two tools: ``apply_answer`` trusts the manifest for
    the key it writes under and for the indices it accepts.
    """
    chapters = [_chapter("8.1", 3), _chapter("11.2", 60)]
    run = _run_dir(tmp_path, chapters)
    work = tmp_path / "work"
    pending_prompts.main(["--dir", str(run), "--work", str(work)])

    manifest = _manifest(work)
    entries = manifest["prompts"]
    assert len(entries) == 3                                  # 1 + 2 blocks
    assert len(list(work.glob("*.txt"))) == 3
    assert manifest["model"] == MODEL
    for e in entries:
        path = work / e["file"]
        text = path.read_text(encoding="utf-8")
        assert e["cache_key"] == _key(text)
        assert e["chars"] == len(text)
        assert e["file"].startswith(f"{entries.index(e) + 1:04d}_")
        assert e["tag"] in e["file"]


# -- part 2: playing an answer back ---------------------------------------------------------

def _exported(tmp_path, pending_prompts, chapters, **kw) -> tuple[Path, Path, dict]:
    run = _run_dir(tmp_path, chapters, **kw)
    work = tmp_path / "work"
    assert pending_prompts.main(["--dir", str(run), "--work", str(work)]) == 0
    return run, work, _manifest(work)


def _write_answer(tmp_path, data: dict | str, name: str = "antwort.json") -> Path:
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_a_valid_answer_lands_in_the_cache(tmp_path, apply_answer, pending_prompts, capsys):
    """The file appears under the key the manifest names, byte for byte the answer."""
    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("8.1", 3)])
    entry = manifest["prompts"][0]
    answer = _answer([0, 1, 2], "8.1")
    path = _write_answer(tmp_path, answer)

    rc = apply_answer.main(["--work", str(work), "--answer", "0001", "--file", str(path)])
    text = capsys.readouterr().out

    assert rc == 0
    cached = run / "llm_cache" / f"{entry['cache_key']}.json"
    assert cached.exists()
    assert json.loads(cached.read_text(encoding="utf-8")) == answer
    assert "0001" in text
    assert "evidence" not in text.lower() or "0" in text     # no evidence warning


def test_a_broken_answer_is_refused(tmp_path, apply_answer, pending_prompts, capsys):
    """Unparsable JSON writes nothing. No third parse attempt, no patching."""
    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("8.1", 3)])
    entry = manifest["prompts"][0]
    path = _write_answer(tmp_path, '{"interpretations": [ {"change_index": 0} ')

    rc = apply_answer.main(["--work", str(work), "--answer", "0001", "--file", str(path)])
    text = capsys.readouterr().out + capsys.readouterr().err

    assert rc != 0
    assert not (run / "llm_cache" / f"{entry['cache_key']}.json").exists()
    assert not list((run / "llm_cache").glob("*"))
    assert "json" in text.lower()


def test_an_out_of_range_change_index_is_refused(tmp_path, apply_answer, pending_prompts,
                                                 capsys):
    """A change the *this* prompt never showed is not a slip, it is a different chapter.

    For a split chapter that means the indices of its own block only -- block 2 answering
    about a change of block 1 would overwrite the better-informed answer on merge.
    """
    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("11.2", 60)])
    second = manifest["prompts"][1]
    foreign = manifest["prompts"][0]["change_indices"][0]
    path = _write_answer(tmp_path, _answer(second["change_indices"][:2] + [foreign], "11.2"))

    rc = apply_answer.main(["--work", str(work), "--answer", "0002", "--file", str(path)])
    text = capsys.readouterr().out + capsys.readouterr().err

    assert rc != 0
    assert not (run / "llm_cache" / f"{second['cache_key']}.json").exists()
    assert str(foreign) in text

    # ... while the same answer without the foreign index is accepted
    ok = _write_answer(tmp_path, _answer(second["change_indices"][:2], "11.2"), "ok.json")
    assert apply_answer.main(["--work", str(work), "--answer", "0002",
                              "--file", str(ok)]) == 0
    assert (run / "llm_cache" / f"{second['cache_key']}.json").exists()


def test_a_pipeline_owned_field_is_dropped(tmp_path, apply_answer, pending_prompts, capsys):
    """What the pipeline knows itself is discarded and counted, never stored (ENT-51)."""
    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("8.1", 3)])
    entry = manifest["prompts"][0]
    answer = _answer([0], "8.1", section_id="wrong-chapter")
    answer["interpretations"][0]["evidence_ok"] = True
    path = _write_answer(tmp_path, answer)

    rc = apply_answer.main(["--work", str(work), "--answer", "0001", "--file", str(path)])
    text = capsys.readouterr().out

    assert rc == 0
    stored = json.loads((run / "llm_cache" / f"{entry['cache_key']}.json")
                        .read_text(encoding="utf-8"))
    assert "section_id" not in stored
    assert "evidence_ok" not in stored["interpretations"][0]
    assert "section_id" in text and "evidence_ok" in text
    assert "2" in text                                   # both are counted


def test_missing_evidence_only_warns(tmp_path, apply_answer, pending_prompts, capsys):
    """A quote that is not in the prompt is reported and accepted.

    The pipeline's evidence guard is the authority on quotes; this is an early warning,
    not a second one -- refusing here would throw away the whole chapter over one line.
    """
    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("8.1", 3)])
    entry = manifest["prompts"][0]
    answer = _answer([0, 1], "8.1")
    answer["interpretations"][0]["evidence"] = "Diesen Satz hat niemand geschrieben."
    path = _write_answer(tmp_path, answer)

    rc = apply_answer.main(["--work", str(work), "--answer", "0001", "--file", str(path)])
    text = capsys.readouterr().out

    assert rc == 0
    stored = json.loads((run / "llm_cache" / f"{entry['cache_key']}.json")
                        .read_text(encoding="utf-8"))
    assert stored["interpretations"][0]["evidence"] == "Diesen Satz hat niemand geschrieben."
    assert "evidence" in text.lower()
    assert "1" in text


def test_the_write_is_atomic(tmp_path, apply_answer, pending_prompts, monkeypatch, capsys):
    """A write that breaks off leaves nothing behind -- no half file, no ``.tmp``."""
    import os

    run, work, manifest = _exported(tmp_path, pending_prompts, [_chapter("8.1", 3)])
    entry = manifest["prompts"][0]
    path = _write_answer(tmp_path, _answer([0], "8.1"))

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    rc = apply_answer.main(["--work", str(work), "--answer", "0001", "--file", str(path)])
    monkeypatch.undo()
    text = capsys.readouterr().out + capsys.readouterr().err

    assert rc != 0
    assert "disk full" in text
    cache_dir = run / "llm_cache"
    assert not (cache_dir / f"{entry['cache_key']}.json").exists()
    assert not list(cache_dir.glob("*.tmp"))
    assert not list(cache_dir.glob("*"))

    # ... and the retry after the fault succeeds
    assert apply_answer.main(["--work", str(work), "--answer", "0001",
                              "--file", str(path)]) == 0
    assert (cache_dir / f"{entry['cache_key']}.json").exists()


def test_the_batch_mode_reads_a_whole_directory(tmp_path, apply_answer, pending_prompts,
                                                capsys):
    """``--batch`` takes ``NNNN.json`` files: one line each, one summary at the end."""
    run, work, manifest = _exported(tmp_path, pending_prompts,
                                    [_chapter("8.1", 3), _chapter("9.3", 2)])
    answers = tmp_path / "answers"
    answers.mkdir()
    for entry in manifest["prompts"]:
        number = entry["file"].split("_", 1)[0]
        (answers / f"{number}.json").write_text(
            json.dumps(_answer(entry["change_indices"], entry["chapter"]),
                       ensure_ascii=False), encoding="utf-8")
    (answers / "0002.json").write_text("kaputt", encoding="utf-8")

    rc = apply_answer.main(["--work", str(work), "--batch", str(answers)])
    text = capsys.readouterr().out

    assert rc != 0                                   # one of them failed
    assert len(list((run / "llm_cache").glob("*.json"))) == 1
    assert "0001" in text and "0002" in text
    assert len([ln for ln in text.splitlines() if ln.strip()]) >= 3


# -- part 3: resumable progress -------------------------------------------------------------

def test_status_counts_what_is_done(tmp_path, pending_prompts, apply_answer, capsys):
    """After two answers ``--status`` reports two fewer, and names what is left."""
    run, work, manifest = _exported(tmp_path, pending_prompts,
                                    [_chapter("8.1", 3), _chapter("9.3", 2),
                                     _chapter("10.4", 2)])
    entries = manifest["prompts"]
    assert len(entries) == 3

    pending_prompts.main(["--dir", str(run), "--work", str(work), "--status"])
    before = capsys.readouterr().out
    assert "3" in before

    for entry in entries[:2]:
        path = _write_answer(tmp_path, _answer(entry["change_indices"], entry["chapter"]),
                             name=f"{entry['file']}.json")
        assert apply_answer.main(["--work", str(work),
                                  "--answer", entry["file"].split("_", 1)[0],
                                  "--file", str(path)]) == 0
    capsys.readouterr()

    rc = pending_prompts.main(["--dir", str(run), "--work", str(work), "--status"])
    after = capsys.readouterr().out

    assert rc == 0
    assert "2" in after and "1" in after
    assert entries[2]["file"] in after                     # the one still missing, by name
    assert entries[0]["file"] not in after                 # the done ones are not listed
