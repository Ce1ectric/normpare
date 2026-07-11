"""normpare — compare two editions of a technical standard.

Public facade of the package: ``compare()`` builds a configuration and runs the full
pipeline in one call.
"""
from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

__all__ = ["__version__", "compare"]


def compare(
    old: str | Path,
    new: str | Path,
    out_dir: str | Path,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    language: str = "de",
    use_llm: bool = True,
    batch: bool = False,
    embed_fallback: bool = False,
    embed_model: str | None = None,
    config: str | Path | None = None,
):
    """Compare two versions of a standard and write all outputs to ``out_dir``.

    One-call facade: builds a :class:`~normpare.config.Config` and runs the
    :class:`~normpare.pipeline.Pipeline`.

    Args:
        old: Path to the old version of the standard (PDF or DOCX).
        new: Path to the new version of the standard (PDF or DOCX).
        out_dir: Target directory for all output.
        provider: LLM provider (``anthropic`` | ``openai`` | ``ollama`` | ``chat``).
        model: Model name at the provider (provider default if omitted).
        language: Language of the compared standard (default ``"de"``).
        use_llm: If ``False``, produce only the deterministic outputs (skip interpretation).
        batch: If ``True``, submit the interpretation as one Anthropic message batch
            (~50% cheaper, asynchronous).
        embed_fallback: If ``True``, run the optional embedding rescue pass (ADR-0001) after
            the deterministic alignment; needs the ``embeddings`` extra. Off by default.
        embed_model: Embedding model for the rescue pass (default if omitted).
        config: Optional configuration file (JSON or TOML); when given it supplies the
            document metadata.

    Returns:
        A ``RunResult`` with the paths of the produced artifacts.
    """
    from .config import Config
    from .pipeline import Pipeline

    if config is not None:
        cfg = Config.from_file(config, out_dir)
        if embed_fallback:
            cfg.embed_fallback = True
        if embed_model:
            cfg.embed_model = embed_model
        if batch:
            cfg.llm.batch = True
    else:
        cfg = Config.for_compare(old, new, out_dir, provider=provider, model=model,
                                 language=language, embed_fallback=embed_fallback,
                                 embed_model=embed_model, batch=batch)
    return Pipeline(cfg).run("all", use_llm=use_llm)
