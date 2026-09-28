"""The package version is declared twice; both declarations must agree.

``pyproject.toml`` feeds the built wheel and PyPI, ``normpare.__version__`` is what a run
reports about itself. A release that bumps only one of them ships a wheel whose runs name
the wrong version -- this test catches that before the tag is set.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import normpare

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_pyproject_and_package_version_agree():
    with PYPROJECT.open("rb") as fh:
        declared = tomllib.load(fh)["tool"]["poetry"]["version"]
    assert normpare.__version__ == declared


def test_changelog_has_a_section_for_the_current_version():
    changelog = (PYPROJECT.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{normpare.__version__}]" in changelog
