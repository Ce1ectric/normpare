"""
replay.py -- recompute the deterministic stages of a reference run.

Copies the frozen inputs of a reference directory (``alt/norm_doc.json``,
``neu/norm_doc.json``, plus ``deutung.json`` and the vector cache as frozen
artifacts) into a destination below ``runs/`` and runs the stages ``map``,
``align``, ``synopse``, ``keywords`` and ``report`` on them. The ingest and enrich
stages are skipped; their result is what the copied documents carry.

What they carry is the state **after** ``map``, not after ``enrich`` (AP-32):
``Pipeline.map`` writes ``alt/norm_doc.json`` back, because the chapter mapping
materializes the titles of absorbed old sections as paragraphs in the document. Those
paragraphs are removed from the copy before the replay starts
(:func:`strip_synthetic_paragraphs`) -- they are marked ``synthetic_title: true``, so
they are recognizable without ambiguity, they carry nothing but the section title, and
``map`` creates them again anyway. Replaying over them would let them take part in the
similarity computation a second time: a different set of sections gets absorbed, new
synthetic paragraphs appear, and the run is continued instead of reproduced (measured
on ``arbeit/60909_v2``: 29 moves became 36).

The interpretation stage is *never* run -- no LLM call, no network. The Hugging
Face libraries are pinned to offline mode, and every vector the embedding rescue
pass needs is already in the copied cache.

The destination is a run directory or, since AP-06, the reference of a baseline
(``baselines/4110_replay``); ``regression.guard_write`` refuses any target below
``out/``, because the frozen reference runs are irreplaceable.

Usage::

    python tools/replay.py --reference out/4110_hot --dest runs/AP-01_2026-08-10/replay
    python tools/replay.py --reference out/4110_hot --dest baselines/4110_replay --enrich
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import regression

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

#: Copied from the reference run, not recomputed.
FROZEN = ("alt/norm_doc.json", "neu/norm_doc.json", "deutung.json", ".vec_cache.npz")

#: Everything except ``ingest``, ``enrich`` (already applied) and ``deutung`` (LLM).
STAGES = ["map", "align", "synopse", "keywords", "report"]


def stages(enrich: bool = False) -> list[str]:
    """The stages to recompute.

    ``enrich`` prepends the enrichment stage. The frozen ``norm_doc.json`` already carries
    the enrichment of the reference run, so re-running it is only needed when the
    enrichment itself changed -- modality, references, values. It rewrites the copied
    files in place; the reference stays untouched either way.
    """
    return (["enrich"] if enrich else []) + STAGES


def _offline() -> None:
    """Pin the embedding stack to local files, so a missing model fails loudly."""
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[var] = "1"


def strip_synthetic_paragraphs(path: Path) -> int:
    """Remove the paragraphs ``map`` inserted into a finished run's document.

    The file layer of :func:`normpare.stages.align.sections.strip_synthetic_paragraphs`,
    which knows the flag because it sets it (AP-33): read, strip, write, report the
    count. The file is only rewritten when there was something to remove, so a document
    without them keeps its bytes.
    """
    from normpare.stages.align.sections import strip_synthetic_paragraphs as strip_doc

    target = regression.guard_write(path)
    doc = json.loads(target.read_text(encoding="utf-8"))
    removed = strip_doc(doc)
    if removed:
        # same shape as ``pipeline._write`` -- the stages rewrite the file anyway
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return removed


def stage_inputs(reference: Path, dest: Path) -> list[str]:
    """Copy the frozen inputs into ``dest``. Returns the names actually copied.

    The copied ``norm_doc.json`` are the state after ``map`` and are put back to the
    state the replay claims to start from: without the synthetic title paragraphs.
    """
    regression.guard_write(dest)
    copied = []
    for name in FROZEN:
        src = reference / name
        if not src.exists():
            continue
        target = regression.guard_write(dest / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        if name.endswith("norm_doc.json"):
            n = strip_synthetic_paragraphs(target)
            print(f"[replay] {name}: {n} synthetic title paragraph(s) removed")
        copied.append(name)
    return copied


def build_config(reference: Path, dest: Path):
    """Rebuild the run configuration from what the reference artifacts record."""
    from normpare.config import Config, SourceSpec

    mapping = json.loads((reference / "mapping.json").read_text(encoding="utf-8"))
    pair = mapping.get("pair") or ""
    old_label, _, new_label = pair.partition(" <-> ")
    # the rescue pass left its trace in the reference mapping, so replay it the same way
    embed = "embed_rescue" in mapping
    return Config(
        old=SourceSpec("", old_label, old_label, "old"),
        new=SourceSpec("", new_label, new_label, "new"),
        out_dir=str(dest), run_name=reference.name, pair_label=pair,
        embed_fallback=embed,
    )


def replay(reference: Path, dest: Path, enrich: bool = False) -> Path:
    """Copy the frozen inputs and recompute the deterministic stages into ``dest``."""
    from normpare.pipeline import Pipeline

    _offline()
    copied = stage_inputs(reference, dest)
    print(f"[replay] frozen inputs copied: {', '.join(copied)}")
    cfg = build_config(reference, dest)
    print(f"[replay] embed rescue pass: {'on' if cfg.embed_fallback else 'off'}")
    todo = stages(enrich)
    Pipeline(cfg).run(todo, use_llm=False)
    print(f"[replay] stages recomputed: {', '.join(todo)}")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reference", required=True, help="reference run directory (read only)")
    ap.add_argument("--dest", required=True, help="destination below runs/")
    ap.add_argument("--baseline", default=None,
                    help="baseline to compare the replay against (default: none)")
    ap.add_argument("--enrich", action="store_true",
                    help="re-run the enrichment stage on the copied norm_doc.json "
                         "(needed when modality, references or values changed)")
    args = ap.parse_args(argv)

    dest = replay(Path(args.reference), Path(args.dest), enrich=args.enrich)
    if not args.baseline:
        return 0
    return regression.check(dest, Path(args.baseline), explain=True)


if __name__ == "__main__":
    raise SystemExit(main())
