"""
asset_recheck.py -- recompute the evidence guard for table and figure interpretations.

``tools/evidence_report.py`` covers paragraph interpretations only. Assets go through
``check_asset_evidence`` against a different haystack (captions, cells and -- since the
review of the 4110 run -- the chapter body), so a change to that rule stays invisible in
the evidence report.

This tool reads a finished run, rebuilds the haystack with the **current** code and
re-runs the guard over the stored answers. It compares the recomputed verdict with the
one stored in ``deutung.json``, which is what makes a rule change measurable without
spending money on a fresh interpretation run.

    poetry run python tools/asset_recheck.py --dir out/4110_2026-08b

Read-only: the run directory is never written to.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normpare.stages.deutung import asset_haystack, check_asset_evidence  # noqa: E402


def _sections(run: Path, side: str) -> dict:
    doc = json.loads((run / side / "norm_doc.json").read_text(encoding="utf-8"))
    return {s["id"]: s for s in doc["sections"]}


def recheck(run: Path) -> tuple[Counter, list[dict]]:
    """Verdict counts and the entries whose verdict changed."""
    deut = json.loads((run / "deutung.json").read_text(encoding="utf-8"))
    chap = json.loads((run / "chapters.json").read_text(encoding="utf-8"))
    by_map = {c.get("mapping_id"): c for c in chap.get("chapters") or []}
    o_secs, n_secs = _sections(run, "alt"), _sections(run, "neu")

    zaehler, geaendert = Counter(), []
    for kapitel in deut.get("chapters") or []:
        ch = by_map.get(kapitel.get("mapping_id"))
        if not ch:
            zaehler["kapitel_ohne_gegenstueck"] += 1
            continue
        hay = asset_haystack(ch, o_secs, n_secs)
        for art in ("tables", "figures"):
            for a in kapitel.get(art) or []:
                vorher = bool(a.get("evidence_ok"))
                nachher = bool(check_asset_evidence(a, hay)["evidence_ok"])
                zaehler[f"{art}_gesamt"] += 1
                zaehler[f"{art}_vorher_ok"] += vorher
                zaehler[f"{art}_nachher_ok"] += nachher
                if vorher != nachher:
                    geaendert.append({
                        "art": art[:-1],
                        "section_id": kapitel.get("section_id"),
                        "richtung": "gerettet" if nachher else "VERLOREN",
                        "evidence": (a.get("evidence") or "")[:160],
                    })
    return zaehler, geaendert


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="finished run directory")
    ap.add_argument("--zeige", type=int, default=15, help="how many changed cases to list")
    args = ap.parse_args(argv)

    run = Path(args.dir)
    z, geaendert = recheck(run)

    print(f"asset evidence recheck -- {run}\n")
    gesamt_v = gesamt_n = gesamt = 0
    for art in ("tables", "figures"):
        n, v, na = z[f"{art}_gesamt"], z[f"{art}_vorher_ok"], z[f"{art}_nachher_ok"]
        gesamt += n; gesamt_v += v; gesamt_n += na
        if n:
            print(f"  {art:9} {v:4d} -> {na:4d} von {n:4d}   "
                  f"({100*v/n:5.1f} % -> {100*na/n:5.1f} %)")
    if gesamt:
        print(f"  {'zusammen':9} {gesamt_v:4d} -> {gesamt_n:4d} von {gesamt:4d}   "
              f"({100*gesamt_v/gesamt:5.1f} % -> {100*gesamt_n/gesamt:5.1f} %)")

    verloren = [g for g in geaendert if g["richtung"] == "VERLOREN"]
    print(f"\n  gerettet {len(geaendert) - len(verloren)}   verloren {len(verloren)}")
    if verloren:
        print("  ACHTUNG: eine Regelaenderung darf nichts verlieren -- Faelle unten pruefen.")
    for g in (verloren + geaendert)[:args.zeige]:
        print(f"    {g['richtung']:9} {g['art']:6} Kap. {g['section_id']:12} {g['evidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
