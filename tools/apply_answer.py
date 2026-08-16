"""
apply_answer.py -- check an interpretation answer and put it into the run's answer cache.

The counterpart of ``tools/pending_prompts.py``: that one writes out the prompts a run
would still ask, this one takes the answers back in. A file under
``<run>/llm_cache/<key>.json`` is what the pipeline reads instead of asking, so the
chapter counts as interpreted from then on.

    poetry run python tools/apply_answer.py --work arbeit/4110 --answer 0007 --file antwort.json
    poetry run python tools/apply_answer.py --work arbeit/4110 --batch antworten/

**Checking, not repairing.** A wrong answer in the cache is worse than a missing one,
because nothing ever questions it again -- so nothing here cuts, balances or patches an
answer into shape. Refused outright are:

1. a file that neither the strict nor the lenient parse reads (``load_json_answer``; a
   ``repaired`` answer is noted, not concealed),
2. a missing ``interpretations`` list -- and, for a prompt that carried an asset block,
   missing ``tables``/``figures``,
3. a ``change_index`` this prompt never showed: for a split chapter that is the index
   range of *this* block only (AP-19),
4. nothing -- a pipeline-owned field (ENT-51) is discarded and counted, the rest of the
   answer stands.

The quote check is a **warning, not a rejection**: whether ``evidence`` occurs in the
prompt is reported and counted, and the answer is stored anyway. The evidence guard of
the pipeline is the authority on quotes; a second one here would throw away a whole
chapter over one line.

The write is atomic (temporary file, then rename), so a broken-off run leaves no half
file behind. Nothing is written until checks 1 to 4 pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normpare.stages.deutung import (
    OUTCOME_REPAIRED,
    PIPELINE_OWNED_ASSET,
    PIPELINE_OWNED_CHAPTER,
    PIPELINE_OWNED_INTERPRETATION,
    drop_pipeline_owned,
    evidence_fragments,
    load_json_answer,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_entry(manifest: dict, number: str) -> dict | None:
    """The manifest entry named by ``0007``, ``7`` or ``0007_11.2.txt``."""
    stem = Path(number).stem
    head = stem.split("_", 1)[0]
    for entry in manifest["prompts"]:
        if entry["file"] == number or entry["file"].startswith(f"{head}_"):
            return entry
        if head.isdigit() and entry["file"].startswith(f"{int(head):04d}_"):
            return entry
    return None


def evidence_misses(data: dict, prompt: str) -> int:
    """How many quotes of this answer do not occur in the prompt verbatim.

    Fragment-wise, as :func:`normpare.stages.deutung.evidence_metrics` does: a quote the
    model joined across an ellipsis counts as found when every fragment is found.
    """
    misses = 0
    for kind in ("interpretations", "tables", "figures"):
        for item in data.get(kind) or []:
            ev = (item or {}).get("evidence") or ""
            if not ev.strip():
                continue
            if not all(f in prompt for f in evidence_fragments(ev)):
                misses += 1
    return misses


def check(entry: dict, data, prompt: str) -> tuple[list[str], Counter]:
    """``(reasons to refuse, discarded pipeline-owned fields)`` for one answer."""
    errors: list[str] = []
    dropped: Counter = Counter()
    if not isinstance(data, dict):
        return [f"the answer is a {type(data).__name__}, not a JSON object"], dropped

    interpretations = data.get("interpretations")
    if not isinstance(interpretations, list):
        errors.append("no 'interpretations' list in the answer")
        interpretations = []
    if entry.get("has_assets") and not any(isinstance(data.get(k), list)
                                           for k in ("tables", "figures")):
        errors.append("the prompt carried an asset block, the answer has neither "
                      "'tables' nor 'figures'")

    allowed = set(entry["change_indices"])
    foreign = [d.get("change_index") for d in interpretations
               if isinstance(d, dict) and d.get("change_index") not in allowed]
    if foreign:
        shown = f"{min(allowed)}..{max(allowed)}" if allowed else "none"
        errors.append(f"change_index {foreign} was not shown by this prompt "
                      f"(this block asked about {len(allowed)} changes, {shown})")

    dropped.update(drop_pipeline_owned(data, PIPELINE_OWNED_CHAPTER).keys())
    for d in interpretations:
        if isinstance(d, dict):
            dropped.update(drop_pipeline_owned(d, PIPELINE_OWNED_INTERPRETATION).keys())
    for kind in ("tables", "figures"):
        for a in data.get(kind) or []:
            if isinstance(a, dict):
                dropped.update(drop_pipeline_owned(a, PIPELINE_OWNED_ASSET).keys())
    return errors, dropped


def write_atomic(path: Path, data: dict) -> None:
    """Write the cache file via a temporary neighbour, then rename.

    A run broken off mid-write leaves neither a half file nor the temporary one -- the
    cache is only ever read again, never checked.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply_one(manifest: dict, work: Path, number: str, answer_file: Path) -> int:
    """Check one answer and store it; 0 on success, 1 on refusal."""
    entry = find_entry(manifest, number)
    if entry is None:
        print(f"{number}: REFUSED -- no such prompt in {work / 'manifest.json'}")
        return 1
    where = f"{entry['file']} (chapter {entry['chapter']}, part {entry['part']}/{entry['n_parts']})"
    prompt_file = work / entry["file"]
    if not prompt_file.exists():
        print(f"{where}: REFUSED -- the prompt file is gone, the quotes cannot be checked")
        return 1
    try:
        data, outcome = load_json_answer(answer_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"{where}: REFUSED -- the file is not readable JSON: {e}")
        return 1

    errors, dropped = check(entry, data, prompt_file.read_text(encoding="utf-8"))
    if errors:
        for reason in errors:
            print(f"{where}: REFUSED -- {reason}")
        return 1

    target = Path(manifest["cache_dir"]) / f"{entry['cache_key']}.json"
    if target.exists():
        print(f"{where}: already in the cache -- skipped")
        return 0
    try:
        write_atomic(target, data)
    except OSError as e:
        print(f"{where}: FAILED -- the write did not go through: {e}")
        return 1

    notes = [f"{len(data.get('interpretations') or [])} interpretation(s)"]
    if outcome == OUTCOME_REPAIRED:
        notes.append("read only by the lenient parse (repaired)")
    if dropped:
        notes.append(f"dropped {sum(dropped.values())} pipeline-owned field(s) ("
                     + ", ".join(f"{f} x{n}" for f, n in sorted(dropped.items())) + ")")
    misses = evidence_misses(data, prompt_file.read_text(encoding="utf-8"))
    if misses:
        notes.append(f"WARNING: {misses} evidence quote(s) not found in the prompt")
    print(f"{where}: ok, " + ", ".join(notes))
    print(f"  -> {target}")
    return 0


def apply_batch(manifest: dict, work: Path, folder: Path) -> int:
    """Every ``NNNN.json`` of a directory: one line per answer, one summary."""
    files = sorted(folder.glob("*.json"))
    if not files:
        print(f"no *.json answers in {folder}")
        return 1
    failed = sum(apply_one(manifest, work, f.stem, f) for f in files)
    print(f"summary: {len(files)} answer(s), {len(files) - failed} applied, {failed} refused")
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True, type=Path,
                    help="the working directory pending_prompts.py wrote")
    ap.add_argument("--answer", help="number of the prompt being answered, e.g. 0007")
    ap.add_argument("--file", type=Path, help="the answer file for --answer")
    ap.add_argument("--batch", type=Path, help="a directory of NNNN.json answers")
    args = ap.parse_args(argv)

    manifest = _load(args.work / "manifest.json")
    if args.batch:
        return apply_batch(manifest, args.work, args.batch)
    if not args.answer or not args.file:
        ap.error("either --answer with --file, or --batch")
    return apply_one(manifest, args.work, args.answer, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
