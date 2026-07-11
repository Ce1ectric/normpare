"""Enables ``python -m normpare …`` (equivalent to the console command)."""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
