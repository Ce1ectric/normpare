"""Run manifest: what produced this output directory (ENT-29).

Every run writes ``manifest.json`` next to its artifacts. Without it an output
directory is not self-explanatory: which version, which model, which ``tau`` produced
it can only be guessed from the artifacts themselves -- which is exactly what happened
with ``out/4110_hot``.

The most important field is :data:`SCHEMA_VERSION`. ``out/4110_hot`` is half migrated
(``deutung.json`` English, everything else German) and nothing in the directory said so;
a schema version would have made that visible on sight (ENT-25).

The manifest carries a timestamp, so it is never byte-identical between two runs and is
excluded from the byte comparison of the regression harness
(``tools/regression.NOT_COMPARED``).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

#: Version of the artifact schema this build writes.
#:
#: ``2`` is today's state: the interpretation stage writes English field names
#: (``training_relevance``, ``semantic_label``, ...). ``1`` was the German schema of
#: Pipeline_v2 (``schulungsrelevanz``, ...), which no build writes any more but which
#: still sits in reference runs from before the migration.
SCHEMA_VERSION = 2


def file_digest(path) -> dict:
    """``{path, sha256, bytes}`` of an input file; hash and size are ``None`` if absent.

    Absent covers anything that is not a readable file -- a replay run has no sources at
    all and carries an empty path, which resolves to the current *directory*.
    """
    p = Path(path)
    if not p.is_file():
        return {"path": str(p), "sha256": None, "bytes": None}
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return {"path": str(p), "sha256": h.hexdigest(), "bytes": p.stat().st_size}


def _version() -> str:
    from . import __version__
    return __version__


def build_manifest(cfg, stages=None, use_llm: bool = True) -> dict:
    """The manifest of a run for configuration ``cfg``."""
    llm = cfg.llm
    return {
        "schema_version": SCHEMA_VERSION,
        "normpare_version": _version(),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_name": cfg.run_name,
        "pair_label": cfg.pair_label,
        "stages": list(stages) if stages is not None else None,
        "use_llm": use_llm,
        "inputs": {
            "old": {**file_digest(cfg.old.path), "doc_id": cfg.old.doc_id,
                    "version_label": cfg.old.version_label},
            "new": {**file_digest(cfg.new.path), "doc_id": cfg.new.doc_id,
                    "version_label": cfg.new.version_label},
        },
        "parameters": {
            "similarity": cfg.similarity,
            "tau": cfg.tau,
            "tau_moved": cfg.tau_moved,
            "language": cfg.language,
            "embed_fallback": cfg.embed_fallback,
            "embed_model": cfg.embed_model,
            "tau_embed": cfg.tau_embed,
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "llm_base_url": llm.base_url,
            "llm_key_env": llm.key_env,
            "llm_api_version": llm.api_version,
            "llm_scope": llm.scope,
            "llm_batch": llm.batch,
        },
    }


def write_manifest(cfg, out_dir, stages=None, use_llm: bool = True) -> Path:
    """Write ``manifest.json`` into ``out_dir`` and return its path."""
    path = Path(out_dir) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(cfg, stages, use_llm),
                               ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path
