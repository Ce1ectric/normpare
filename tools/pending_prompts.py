"""
pending_prompts.py -- export the prompts a run would still ask, for interpretation
without an API.

The answer cache is keyed on ``model + system prompt + user prompt`` and knows nothing
about the provider (:func:`normpare.stages.deutung.cache_key`), so a JSON file under the
right key *is* an interpretation: the pipeline will not ask anybody about that chapter
again. This tool names the chapters that still have no such file.

    poetry run python tools/pending_prompts.py --dir out/4110_final --work arbeit/4110
    poetry run python tools/pending_prompts.py --dir out/4110_final --work arbeit/4110 --status

Every prompt is built with the production code
(:func:`normpare.stages.deutung.chapter_jobs`), split blocks included (AP-19), so an
exported prompt is character-identical to the one a run would send.

**The key self-check comes first and decides everything else.** For the answers already
in the cache the keys are rebuilt and compared with the file names. Model and system
prompt enter every key alike, so if either is wrong *no* cached answer can be found --
and an export under such a key would produce answers the pipeline never picks up, which
would only show after hours of interpretation work. Then the tool aborts and writes
nothing. Prompts whose *body* has changed since the answer was written (AP-20 raised the
table budgets) are a different fact: those are reported as drift and asked again, which
is exactly what a run would do.

Read-only on the run directory: nothing under ``--dir`` is opened for writing, no LLM is
called, no socket is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normpare.config import DEFAULT_MODEL
from normpare.stages.deutung import (
    build_system_prompt,
    cache_key,
    chapter_jobs,
    section_index,
)

#: Where the title of the compared standard in the system prompt comes from. The pipeline
#: uses ``cfg.new.title or cfg.pair_label``; a finished run keeps the doc id, not the
#: title, so the candidates are tried and the one the cache confirms is reported.
TITLE_SOURCES = ("new doc_id", "pair_label", "empty")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def titles(manifest: dict) -> dict[str, str]:
    """The title candidates for the system prompt, most likely first."""
    return {"new doc_id": manifest.get("inputs", {}).get("new", {}).get("doc_id", ""),
            "pair_label": manifest.get("pair_label", ""),
            "empty": ""}


def prompts_of(run: Path, scope: str | None = None) -> list[tuple]:
    """``(tag, chapter, prompt, change indices, part, n_parts, has assets)`` per request."""
    manifest = _load(run / "manifest.json")
    synopse = _load(run / "synopse.json")
    o_secs = section_index(_load(run / "alt" / "norm_doc.json"))
    n_secs = section_index(_load(run / "neu" / "norm_doc.json"))
    scope = scope or manifest.get("parameters", {}).get("llm_scope", "core")
    out = []
    for job in chapter_jobs(synopse, o_secs, n_secs, scope):
        for n, (tag, (prompt, sel)) in enumerate(zip(job.tags, job.parts), start=1):
            out.append((tag, job.section_id, prompt, sel, n, len(job.parts),
                        "TABELLEN & BILDER" in prompt))
    return out


def key_check(requests: list, model: str, language: str, candidates: dict[str, str],
              cached: set[str]) -> tuple[str, str, str, int]:
    """``(title source, title, system prompt, hits)`` for the best-confirmed candidate.

    "Best" is the candidate whose rebuilt keys are found in the cache most often. With an
    empty cache nothing can be confirmed and the pipeline's own first choice wins.
    """
    best = None
    for source in TITLE_SOURCES:
        title = candidates.get(source, "")
        system = build_system_prompt(language, title)
        hits = sum(1 for _t, _c, prompt, *_ in requests
                   if cache_key(model, system, prompt) in cached)
        if best is None or hits > best[3]:
            best = (source, title, system, hits)
    return best


def percentile(values: list[int], q: float) -> int:
    """The ``q`` quantile of ``values`` (nearest rank), 0 for an empty list."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-q * len(ordered) // 1))))
    return ordered[rank - 1]


def export(run: Path, work: Path, model: str | None = None, force: bool = False) -> int:
    """Write every open prompt plus the manifest; return the process exit code."""
    manifest = _load(run / "manifest.json")
    language = manifest.get("parameters", {}).get("language", "de")
    # the model of *this* run beats the package default: every key carries it, so a run
    # made with another model would otherwise miss its own cache entirely
    run_model = manifest.get("parameters", {}).get("llm_model")
    model = model or run_model or DEFAULT_MODEL
    cache_dir = run / "llm_cache"
    cached = {p.stem for p in cache_dir.glob("*.json")}
    requests = prompts_of(run)

    source, title, system, hits = key_check(requests, model, language, titles(manifest),
                                            cached)
    print(f"run:    {run}")
    print(f"model:  {model!r}" + ("" if model == run_model
                                  else f"   (the run says {run_model!r})"))
    print(f"system: sha1 {hashlib.sha1(system.encode()).hexdigest()[:12]}, language {language!r}, "
          f"title from {source!r}")
    if cached and not hits:
        print(f"KEY CHECK FAILED: none of the {len(cached)} cached answers is hit by a "
              f"rebuilt prompt.")
        print(f"  model {model!r} or the system prompt is wrong -- both enter every key "
              f"alike, so a wrong one misses all of them.")
        print("  title candidates tried: "
              + ", ".join(f"{s!r}" for s in TITLE_SOURCES))
        print("  Nothing was written: an export under this key would produce answers the "
              "pipeline never finds.")
        return 2
    print(f"key check: {hits} of {len(cached)} cached answers are hit by a rebuilt prompt")
    stale = len(cached) - hits
    if stale:
        print(f"  note: {stale} cached answer(s) are hit by no prompt of today's code -- "
              "their prompt changed since; those chapters are asked again, as a run would.")

    open_requests = [r for r in requests
                     if cache_key(model, system, r[2]) not in cached]
    chars = [len(r[2]) for r in open_requests]
    print(f"prompts: {len(requests)} total, {len(requests) - len(open_requests)} cached, "
          f"{len(open_requests)} open")
    print(f"chars:   {sum(chars)} total, median {int(statistics.median(chars)) if chars else 0}, "
          f"p95 {percentile(chars, 0.95)}, max {max(chars, default=0)}")

    if work.exists() and (work / "manifest.json").exists() and not force:
        print(f"refusing to overwrite the existing export in {work} -- use --status to see "
              "the progress, or --force to export again.")
        return 3
    work.mkdir(parents=True, exist_ok=True)
    # the chapter files hold the user prompt only; the instructions -- how to interpret,
    # what evidence is, which language -- are in the system prompt, and it is half of
    # every key, so it travels with the export
    (work / "system_prompt.txt").write_text(system, encoding="utf-8")
    entries = []
    for n, (tag, chapter, prompt, sel, part, n_parts, assets) in enumerate(open_requests,
                                                                          start=1):
        name = f"{n:04d}_{tag}.txt"
        (work / name).write_text(prompt, encoding="utf-8")
        entries.append({"file": name, "tag": tag, "cache_key": cache_key(model, system, prompt),
                        "chars": len(prompt), "chapter": chapter,
                        "part": part, "n_parts": n_parts,
                        "change_indices": sel, "has_assets": assets})
    # no timestamp: the manifest stays byte-reproducible for the same run and model
    (work / "manifest.json").write_text(json.dumps(
        {"run": str(run), "cache_dir": str(cache_dir), "model": model, "language": language,
         "title": title, "title_source": source,
         "n_requests": len(requests), "n_cached": len(requests) - len(open_requests),
         "prompts": entries}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {len(entries)} prompt file(s) + system_prompt.txt + manifest.json "
          f"in {work}")
    return 0


def status(work: Path, cache_dir: Path | None = None) -> int:
    """What of the exported prompts is answered by now -- the cache is the progress."""
    manifest = _load(work / "manifest.json")
    cache = cache_dir or Path(manifest["cache_dir"])
    entries = manifest["prompts"]
    missing = [e for e in entries if not (cache / f"{e['cache_key']}.json").exists()]
    print(f"work:   {work}")
    print(f"cache:  {cache}")
    print(f"status: {len(entries)} exported, {len(entries) - len(missing)} now in the "
          f"cache, {len(missing)} missing")
    if missing:
        print("missing:")
        for e in missing:
            print(f"  {e['file']}  ({e['chars']} chars, chapter {e['chapter']}, "
                  f"part {e['part']}/{e['n_parts']})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, type=Path, help="a finished run directory")
    ap.add_argument("--work", required=True, type=Path, help="where the prompts go")
    ap.add_argument("--model", default=None,
                    help=f"model the keys are built with (default: the model of the run, "
                         f"else {DEFAULT_MODEL})")
    ap.add_argument("--status", action="store_true", help="only report the progress")
    ap.add_argument("--force", action="store_true", help="export again over an existing work dir")
    args = ap.parse_args(argv)
    if args.status:
        return status(args.work, args.dir / "llm_cache")
    return export(args.dir, args.work, args.model, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
