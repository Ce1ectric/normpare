"""Command-line entry point for normpare.

- ``normpare --version``
- ``normpare inspect PATH`` -- readable summary of a norm_doc.json.
- ``normpare compare --old OLD --new NEW --out OUT`` -- run the full comparison.
"""
from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (factored out so tests can inspect it)."""
    parser = argparse.ArgumentParser(
        prog="normpare",
        description="Compare two editions of a technical standard.",
    )
    parser.add_argument("--version", action="version", version=f"normpare {__version__}")

    sub = parser.add_subparsers(dest="command")

    compare = sub.add_parser("compare", help="Compare two versions of a standard.")
    compare.add_argument("--old", help="old version of the standard (PDF or DOCX)")
    compare.add_argument("--new", help="new version of the standard (PDF or DOCX)")
    compare.add_argument("--out", required=True, help="target directory for the output")
    compare.add_argument("--provider", default="anthropic",
                         choices=["anthropic", "openai", "azure_openai", "google", "mistral",
                                  "groq", "together", "openrouter", "ollama",
                                  "openai_compatible", "chat"],
                         help="LLM backend; everything except 'anthropic' uses the "
                              "OpenAI-compatible /chat/completions API. 'chat' only exports "
                              "the prompts for copy-paste.")
    compare.add_argument("--model", default=None, help="model name at the provider")
    compare.add_argument("--base-url", default=None,
                         help="override the provider's API base URL (required for "
                              "azure_openai / openai_compatible, e.g. a custom Ollama host)")
    compare.add_argument("--language", default="de", help="language of the standard (default de)")
    compare.add_argument("--no-llm", action="store_true", help="deterministic outputs only")
    compare.add_argument("--batch", action="store_true",
                         help="submit the interpretation as one Anthropic message batch "
                              "(~50%% cheaper, asynchronous)")
    compare.add_argument("--embed-fallback", action="store_true",
                         help="optional embedding rescue pass (ADR-0001); needs the "
                              "'embeddings' extra. Off by default (deterministic).")
    compare.add_argument("--embed-model", default=None,
                         help="embedding model for the rescue pass")
    compare.add_argument("--config", default=None, help="configuration file (JSON or TOML)")

    inspect = sub.add_parser("inspect", help="Print a readable summary of a norm_doc.json.")
    inspect.add_argument("path", help="path to a norm_doc.json (output of the ingest stage)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Program entry point. The return value is the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        from .inspector import inspect_path
        print(inspect_path(args.path))
        return 0
    if args.command == "compare":
        from .config import Config
        from .pipeline import Pipeline
        if not args.config and not (args.old and args.new):
            parser.error("compare requires --old and --new, or --config")
        if args.config:
            cfg = Config.from_file(args.config, args.out)
            if args.embed_fallback:
                cfg.embed_fallback = True
            if args.embed_model:
                cfg.embed_model = args.embed_model
            if args.batch:
                cfg.llm.batch = True
            if args.base_url:
                cfg.llm.base_url = args.base_url
        else:
            cfg = Config.for_compare(args.old, args.new, args.out, provider=args.provider,
                                     model=args.model, language=args.language,
                                     embed_fallback=args.embed_fallback,
                                     embed_model=args.embed_model, batch=args.batch)
            if args.base_url:
                cfg.llm.base_url = args.base_url
        result = Pipeline(cfg).run("all", use_llm=not args.no_llm)
        print(f"Done. Output written to {result.out_dir}")
        print(f"  HTML                     : {result.html}")
        print(f"  Synopsis (deterministic) : {result.synopse_det}")
        print(f"  Synopsis (final / LLM)   : {result.synopse_final}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
