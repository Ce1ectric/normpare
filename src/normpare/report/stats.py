"""
stats.py -- document and comparison statistics (statistics.json).
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path


def doc_stats(doc: dict) -> dict:
    parts = Counter()
    paras = chars = 0
    tables = figures = formulas = 0
    modality = Counter()
    ext_refs = Counter()
    for s in doc["sections"]:
        parts[s["part"]] += 1
        paras += len(s["paragraphs"])
        tables += len(s.get("tables", []))
        figures += len(s.get("figures", []))
        formulas += len(s.get("formulas", []))
        for p in s["paragraphs"]:
            chars += len(p.get("n1", p.get("n0", "")))
            modality[p.get("modality", "informativ")] += 1
            for r in p.get("refs_external", []):
                ext_refs[r] += 1
    return {"doc_id": doc["doc_id"], "title": doc["title"],
            "source_format": doc["source"]["format"],
            "pages": doc["source"].get("pages"),
            "sections_total": len(doc["sections"]),
            "sections_by_part": dict(parts),
            "paragraphs": paras, "characters": chars,
            "tables": tables, "figures": figures, "formulas": formulas,
            "modality": dict(modality),
            "top_external_refs": ext_refs.most_common(25)}


def pair_stats(synopse: dict) -> dict:
    kinds = Counter()
    kennwert_changes = []
    mod_shifts = Counter()
    part_changes = []
    for ch in synopse["chapters"]:
        cid = ch.get("new_id") or ch.get("old_id")
        if ch.get("part_changed"):
            part_changes.append(cid)
        for c in ch["changes"]:
            kinds[c["kind"]] += 1
            for k in (c.get("kennwerte") or {}).get("changed", []):
                kennwert_changes.append({"chapter": cid,
                                         "old": k["old"]["raw"], "new": k["new"]["raw"]})
            sh = (c.get("modality") or {}).get("shift")
            if sh and sh != "unveraendert":
                mod_shifts[sh] += 1
    return {"change_kinds": dict(kinds),
            "n_chapters_with_changes": sum(1 for ch in synopse["chapters"] if ch["changes"]),
            "kennwert_changes": kennwert_changes,
            "modality_shifts": dict(mod_shifts),
            "part_changed_chapters": part_changes}


def build_statistics(old_doc, new_doc, synopse, out_path: str | Path) -> dict:
    res = {"old": doc_stats(old_doc), "new": doc_stats(new_doc),
           "comparison": pair_stats(synopse)}
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res
