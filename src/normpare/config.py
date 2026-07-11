"""Configuration model for a comparison run.

Built either from explicit arguments (:meth:`Config.for_compare`, used by ``compare()``
and the CLI) or loaded from a JSON/TOML configuration file (:meth:`Config.from_file`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5"


@dataclass
class SourceSpec:
    """One source document (old or new) with its metadata."""

    path: str
    doc_id: str
    title: str
    version_label: str


@dataclass
class LlmConfig:
    """LLM backend settings for the interpretation stage."""

    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    key_env: str | None = None
    api_version: str | None = None
    scope: str = "core"
    batch: bool = False


@dataclass
class Config:
    """Everything a single comparison run needs."""

    old: SourceSpec
    new: SourceSpec
    out_dir: str
    run_name: str = "run"
    pair_label: str = ""
    similarity: str = "tfidf"
    tau: float = 0.62
    tau_moved: float = 0.72
    language: str = "de"
    embed_fallback: bool = False
    embed_model: str = "jinaai/jina-embeddings-v2-base-de"
    tau_embed: float = 0.82
    llm: LlmConfig = field(default_factory=LlmConfig)

    @classmethod
    def for_compare(cls, old, new, out_dir, *, provider: str = "anthropic",
                    model: str | None = None, language: str = "de",
                    run_name: str | None = None, pair_label: str | None = None,
                    embed_fallback: bool = False, embed_model: str | None = None,
                    tau_embed: float = 0.82, batch: bool = False) -> "Config":
        """Build a config from explicit paths, deriving document metadata from the filenames."""
        o, n = Path(old), Path(new)
        return cls(
            old=SourceSpec(str(old), o.stem, o.stem, "old"),
            new=SourceSpec(str(new), n.stem, n.stem, "new"),
            out_dir=str(out_dir),
            run_name=run_name or (Path(out_dir).name or "run"),
            pair_label=pair_label or f"{o.stem} <-> {n.stem}",
            language=language,
            embed_fallback=embed_fallback,
            embed_model=embed_model or "jinaai/jina-embeddings-v2-base-de",
            tau_embed=tau_embed,
            llm=LlmConfig(provider=provider, model=model or DEFAULT_MODEL, batch=batch),
        )

    @classmethod
    def from_file(cls, path, out_dir) -> "Config":
        """Load a config from a JSON or TOML file (see the documentation for the layout)."""
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".toml":
            import tomllib
            data = tomllib.loads(text)
        else:
            data = json.loads(text)

        def _spec(d: dict) -> SourceSpec:
            return SourceSpec(d["path"], d["doc_id"], d["title"], d["version_label"])

        return cls(
            old=_spec(data["old"]), new=_spec(data["new"]), out_dir=str(out_dir),
            run_name=data.get("run_name", "run"), pair_label=data.get("pair_label", ""),
            similarity=data.get("similarity", "tfidf"),
            tau=data.get("tau", 0.62), tau_moved=data.get("tau_moved", 0.72),
            language=data.get("language", "de"),
            embed_fallback=data.get("embed_fallback", False),
            embed_model=data.get("embed_model", "jinaai/jina-embeddings-v2-base-de"),
            tau_embed=data.get("tau_embed", 0.82),
            llm=LlmConfig(
                provider=data.get("llm_provider", "anthropic"),
                model=data.get("llm_model", DEFAULT_MODEL),
                base_url=data.get("llm_base_url"), key_env=data.get("llm_key_env"),
                api_version=data.get("llm_api_version"), scope=data.get("deutung_scope", "core"),
                batch=data.get("llm_batch", False),
            ),
        )
