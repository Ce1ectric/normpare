"""
mapping_balance.py -- how much of the "removed" stream comes out of implausible chapter
mappings.

``align_chapter`` pairs paragraphs one to one. A mapping that puts 89 old paragraphs
against 9 new ones therefore has to report the 80 leftovers as ``removed``, whatever the
text says -- the fault sits one level up, in the chapter mapping, not in the paragraph
aligner. ``diff.paragraph_balance`` measures that mass ratio; this tool aggregates it over
a finished run and shows how the two thresholds (``UNBALANCED_MIN``, ``BALANCE_MIN_SIZE``)
would separate.

    poetry run python tools/mapping_balance.py --dir out/4110_2026-08b

The balance is **recomputed** from the frozen ``norm_doc.json`` of the run, so runs
produced before the field existed can be evaluated too; where the run already carries
``paragraph_balance``, the recomputation is compared against it.

Read-only: the run directory is never written to. No LLM, no network.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normpare.stages.diff import (  # noqa: E402
    BALANCE_MIN_SIZE, UNBALANCED_MIN, paragraph_balance,
)

SCHWELLEN = (0.4, 0.5, 0.6, 0.7, 0.8)
MINDESTGROESSEN = (0, 3, 5, 10)


def _sections(run: Path, side: str) -> dict:
    doc = json.loads((run / side / "norm_doc.json").read_text(encoding="utf-8"))
    return {s["id"]: s for s in doc["sections"]}


def collect(run: Path) -> tuple[list[dict], int]:
    """One row per chapter mapping, plus the number of stored balances that disagree."""
    syn = json.loads((run / "synopse.json").read_text(encoding="utf-8"))
    o_secs, n_secs = _sections(run, "alt"), _sections(run, "neu")

    rows, abweichend = [], 0
    for ch in syn.get("chapters") or []:
        bal = paragraph_balance(
            [o_secs[i] for i in (ch.get("old_ids") or []) if i in o_secs],
            [n_secs[i] for i in (ch.get("new_ids") or []) if i in n_secs])
        if ch.get("paragraph_balance") not in (None, bal):
            abweichend += 1
        rows.append({"mapping_id": ch.get("mapping_id") or ch.get("new_id") or ch.get("old_id"),
                     "mode": ch.get("mode"),
                     "map_confidence": ch.get("map_confidence"),
                     "removed": sum(1 for c in ch.get("changes") or []
                                    if c["kind"] == "removed"),
                     **bal})
    return rows, abweichend


def _unwuchtig(row: dict, schwelle: float, mindest: int) -> bool:
    return row["imbalance"] >= schwelle and max(row["n_old"], row["n_new"]) >= mindest


def report(run: Path, rows: list[dict], abweichend: int, zeige: int) -> None:
    removed_gesamt = sum(r["removed"] for r in rows)
    print(f"mapping balance -- {run}")
    print(f"  {len(rows)} Zuordnungen, {removed_gesamt} removed-Meldungen")
    if abweichend:
        print(f"  ACHTUNG: {abweichend} gespeicherte paragraph_balance weichen von der "
              f"Nachrechnung ab")
    print()

    # 1) distribution of the imbalance -------------------------------------------------
    print("1) Verteilung der Unwucht (Stufen 0,1)")
    print("     Bereich        Zuordnungen   removed")
    for k in range(10):
        lo, hi = k / 10, (k + 1) / 10
        gruppe = [r for r in rows if lo <= r["imbalance"] < hi]
        print(f"     {lo:.1f}-{hi:.1f}      {len(gruppe):8d}  {sum(r['removed'] for r in gruppe):8d}")
    eins = [r for r in rows if r["imbalance"] >= 1.0]
    print(f"     1.0 (ganz)   {len(eins):8d}  {sum(r['removed'] for r in eins):8d}")
    print()

    # 2) the operating point ------------------------------------------------------------
    treffer = [r for r in rows if r["unbalanced"]]
    betroffen = sum(r["removed"] for r in treffer)
    anteil = 100 * betroffen / removed_gesamt if removed_gesamt else 0.0
    print(f"2) Schwelle {UNBALANCED_MIN} bei Mindestgroesse {BALANCE_MIN_SIZE}")
    print(f"     unwuchtige Zuordnungen  {len(treffer)} von {len(rows)}")
    print(f"     betroffene removed      {betroffen} von {removed_gesamt} ({anteil:.1f} %)")
    # the finding in notizen/Dritter_Korpus_60909.md counted only mappings that have a
    # chapter on both sides -- printed separately so both numbers stay comparable
    zwei = [r for r in rows if r["n_old"] and r["n_new"]]
    z_removed = sum(r["removed"] for r in zwei)
    z_betroffen = sum(r["removed"] for r in zwei if r["unbalanced"])
    z_anteil = 100 * z_betroffen / z_removed if z_removed else 0.0
    print(f"     nur zweiseitige Zuordnungen: {sum(1 for r in zwei if r['unbalanced'])} von "
          f"{len(zwei)}, removed {z_betroffen} von {z_removed} ({z_anteil:.1f} %)")
    print()

    # 3) threshold matrix ----------------------------------------------------------------
    print("3) Matrix: Zuordnungen / betroffene removed in Prozent")
    print("     Schwelle  " + "".join(f"  min={m:<11d}" for m in MINDESTGROESSEN))
    for s in SCHWELLEN:
        zellen = []
        for m in MINDESTGROESSEN:
            t = [r for r in rows if _unwuchtig(r, s, m)]
            b = sum(r["removed"] for r in t)
            p = 100 * b / removed_gesamt if removed_gesamt else 0.0
            zellen.append(f"  {len(t):4d} / {p:5.1f} %")
        print(f"     {s:>8.1f}" + "".join(zellen))
    print()

    # 4) what the pure surplus explains ---------------------------------------------------
    print("4) Erklaerungsanteil des reinen Alt-Ueberschusses")
    for name, menge in (("alle Zuordnungen", rows), ("nur unwuchtige", treffer)):
        s_surplus = sum(r["old_surplus"] for r in menge)
        s_removed = sum(r["removed"] for r in menge)
        q = 100 * min(s_surplus, s_removed) / s_removed if s_removed else 0.0
        print(f"     {name:18} old_surplus {s_surplus:5d}  removed {s_removed:5d}  -> {q:5.1f} %")
    print()

    # 5) the most imbalanced mappings ------------------------------------------------------
    print(f"5) Die {zeige} unwuchtigsten Zuordnungen")
    print("     mapping_id                                     n_old  n_new  removed  conf")
    for r in sorted(rows, key=lambda r: (-r["imbalance"], -max(r["n_old"], r["n_new"])))[:zeige]:
        conf = "-" if r["map_confidence"] is None else f"{r['map_confidence']:.3f}"
        print(f"     {str(r['mapping_id'])[:44]:44}  {r['n_old']:5d}  {r['n_new']:5d}  "
              f"{r['removed']:7d}  {conf:>6}")
    print()

    # 5b) the collecting buckets: unbalanced and loud ----------------------------------------
    print(f"5b) Die {zeige} unwuchtigen Zuordnungen mit den meisten removed")
    print("     mapping_id                                     n_old  n_new  removed  conf")
    for r in sorted(treffer, key=lambda r: -r["removed"])[:zeige]:
        conf = "-" if r["map_confidence"] is None else f"{r['map_confidence']:.3f}"
        print(f"     {str(r['mapping_id'])[:44]:44}  {r['n_old']:5d}  {r['n_new']:5d}  "
              f"{r['removed']:7d}  {conf:>6}")
    print()

    # 6) how blind is map_confidence, and is imbalance alone enough? -------------------------
    voll = [r for r in treffer if r["map_confidence"] == 1.0]
    ohne = [r for r in treffer if r["removed"] == 0]
    wenig = [r for r in treffer if r["removed"] <= 2]
    print("6) Gegenproben")
    print(f"     unwuchtig und map_confidence == 1.0   {len(voll)} von {len(treffer)}")
    print(f"     unwuchtig ohne jede removed-Meldung   {len(ohne)} von {len(treffer)}")
    print(f"     unwuchtig mit hoechstens 2 removed    {len(wenig)} von {len(treffer)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, help="finished run directory")
    ap.add_argument("--zeige", type=int, default=15, help="how many mappings to list")
    args = ap.parse_args(argv)

    run = Path(args.dir)
    rows, abweichend = collect(run)
    report(run, rows, abweichend, args.zeige)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
