"""Smoke test: package imports, version well-formed, public API present, CLI builds."""
from __future__ import annotations

import re

import normpare
from normpare.cli import build_parser


def test_version_is_well_formed():
    assert isinstance(normpare.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", normpare.__version__)


def test_compare_is_callable():
    assert callable(normpare.compare)


def test_cli_parser_builds_and_reads_compare_arguments():
    parser = build_parser()
    args = parser.parse_args(
        ["compare", "--old", "a.pdf", "--new", "b.docx", "--out", "z/"]
    )
    assert args.command == "compare"
    assert args.old == "a.pdf"
    assert args.new == "b.docx"
    assert args.out == "z/"
    assert args.provider == "anthropic"  # default
    assert args.language == "de"  # default


def test_cli_parser_accepts_config_without_old_new():
    parser = build_parser()
    args = parser.parse_args(["compare", "--config", "c.json", "--out", "o/"])
    assert args.command == "compare"
    assert args.config == "c.json"
    assert args.old is None and args.new is None
