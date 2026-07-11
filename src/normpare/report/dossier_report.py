"""
dossier.py -- chapter dossier (chapters.json): the central per-chapter view (point 8).

Per chapter: title, part (normative/informative), keywords, summary, change list,
parameter values and references -- everything about a chapter in one place.
"""
from __future__ import annotations
import json
from pathlib import Path


def build_dossier(new_doc: dict, old_doc: dict, synopse: dict, deutung: dict | None,
                  out_path: str | Path) -> dict:
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    deut_by_id = {d.get("section_id"): d for d in (deutung or {}).get("chapters", [])}

    chapters = []
    for ch in synopse["chapters"]:
        cid = ch.get("new_id") or ch.get("old_id")
        sec = n_secs.get(ch.get("new_id")) or o_secs.get(ch.get("old_id")) or {}
        d = deut_by_id.get(cid, {})
        subst = [c for c in ch["changes"] if c["kind"] != "cosmetic"]
        kennwerte = []
        for c in ch["changes"]:
            for k in (c.get("kennwerte") or {}).get("changed", []):
                kennwerte.append({"old": k["old"]["raw"], "new": k["new"]["raw"]})
        chapters.append({
            "id": cid,
            "old_ids": ch.get("old_ids"), "new_ids": ch.get("new_ids"),
            "title": ch.get("title"),
            "level": ch.get("level"),
            "part": ch.get("part"),
            "part_changed": ch.get("part_changed", False),
            "mode": ch["mode"],
            "keywords": list(dict.fromkeys((d.get("keywords") or []) + sec.get("keywords", []))),
            "summary_old": d.get("summary_old"),
            "summary_new": d.get("summary_new"),
            "change_overview": d.get("change_overview"),
            "training_relevance": d.get("training_relevance"),
            "practical_note": d.get("practical_note"),
            "refs_external": sec.get("refs_external", []),
            "refs_internal": sec.get("refs_internal", []),
            "n_changes": len(subst),
            "n_identical": ch.get("n_identical", 0),
            "value_changes": kennwerte,
            "formulas_diff": ch.get("formulas_diff"),
            "tables_diff": [t for t in ch.get("tables_diff", [])
                            if t["kind"] != "matched" or not t.get("identical")],
            "tables_interpretation": d.get("tables") or [],
            "figures_interpretation": d.get("figures") or [],
            "changes": ch["changes"],
        })
    res = {"pair": synopse["pair"], "chapters": chapters}
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res
