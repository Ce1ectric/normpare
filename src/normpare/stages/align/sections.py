"""
align_sections.py -- stage S3a: chapter mapping old <-> new, incl. split/merge candidates.

Passes:
  1  ID+title      same section id + title similarity           (conf = title sim)
  2  title global  unmapped via Hungarian on title+content sim  (threshold)
  3  sequence      gaps between mapped neighbors
  4  split/merge   removed chapter <-> several new subchapters (and vice versa):
                   content similarity of the old text against the union of candidates
Match types: id+title | title | sequence | split | merge | new | removed
A normative<->informative (part) switch is reported as a flag.
"""
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from ...text.textnorm import n3

_STOP = {"und", "oder", "der", "die", "das", "des", "dem", "den", "ein", "eine", "einer",
         "von", "zur", "zum", "zu", "am", "im", "in", "an", "auf", "bei", "mit", "für",
         "durch", "als", "wie", "sowie", "auch", "nur", "sind", "ist", "wird", "werden",
         "allgemeines", "allgemeine", "anforderungen"}


def _title_sim(a: str, b: str) -> float:
    na, nb = n3(a), n3(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ka = {w for w in na.split() if len(w) > 2 and w not in _STOP}
    kb = {w for w in nb.split() if len(w) > 2 and w not in _STOP}
    jac = len(ka & kb) / len(ka | kb) if (ka or kb) else 0.0
    return 0.6 * jac + 0.4 * SequenceMatcher(None, na, nb).ratio()


def _sec_text(sec, max_chars=1500) -> str:
    return (sec["title"] + " " + " ".join(p.get("n1", p["n0"]) for p in sec["paragraphs"]))[:max_chars]


def _generic(title: str) -> bool:
    return n3(title) in {"allgemeines", "allgemeine anforderungen", "anforderungen", "grundsätze"}


def build_section_mapping(old_doc: dict, new_doc: dict, sim_backend,
                          thresh_pass2: float = 0.50, thresh_seq: float = 0.35,
                          thresh_splitmerge: float = 0.45) -> list[dict]:
    olds = [s for s in old_doc["sections"] if s["part"] not in ("vorspann",)]
    news = [s for s in new_doc["sections"] if s["part"] not in ("vorspann",)]
    o_by_id = {s["id"]: i for i, s in enumerate(olds)}
    n_by_id = {s["id"]: i for i, s in enumerate(news)}

    o2n: dict[int, dict] = {}
    used_new: set[int] = set()

    # ---- Pass 1: same ID + title plausibility -----------------------------
    for oi, o in enumerate(olds):
        ni = n_by_id.get(o["id"])
        if ni is None:
            continue
        ts = _title_sim(o["title"], news[ni]["title"])
        # accept generic titles ("Allgemeines") only with neighborhood context
        if ts >= 0.55 or (_generic(o["title"]) and _generic(news[ni]["title"])):
            o2n[oi] = {"ni": ni, "type": "id+title", "conf": round(ts, 3)}
            used_new.add(ni)

    # ---- Pass 2: globally optimal title+content assignment (Hungarian) ----------
    rem_o = [i for i in range(len(olds)) if i not in o2n]
    rem_n = [j for j in range(len(news)) if j not in used_new]
    if rem_o and rem_n:
        content_sim = sim_backend.sim_matrix([_sec_text(olds[i]) for i in rem_o],
                                             [_sec_text(news[j]) for j in rem_n])
        m = np.zeros((len(rem_o), len(rem_n)))
        for a, i in enumerate(rem_o):
            for b, j in enumerate(rem_n):
                if abs(olds[i]["level"] - news[j]["level"]) > 1:
                    continue
                m[a, b] = 0.5 * _title_sim(olds[i]["title"], news[j]["title"]) + 0.5 * content_sim[a, b]
        ri, cj = linear_sum_assignment(-m)
        for a, b in zip(ri, cj):
            if m[a, b] >= thresh_pass2:
                o2n[rem_o[a]] = {"ni": rem_n[b], "type": "title", "conf": round(float(m[a, b]), 3)}
                used_new.add(rem_n[b])

    # ---- Pass 3: sequence gaps ------------------------------------------------
    for oi, o in enumerate(olds):
        if oi in o2n:
            continue
        prev_n = next((o2n[j]["ni"] for j in range(oi - 1, max(-1, oi - 4), -1) if j in o2n), None)
        next_n = next((o2n[j]["ni"] for j in range(oi + 1, min(len(olds), oi + 4)) if j in o2n), None)
        if prev_n is None or next_n is None:
            continue
        gap = [g for g in range(prev_n + 1, next_n) if g not in used_new]
        best, bs = None, 0.0
        for g in gap:
            s = _title_sim(o["title"], news[g]["title"])
            if s > bs:
                best, bs = g, s
        if best is not None and bs >= thresh_seq:
            o2n[oi] = {"ni": best, "type": "sequence", "conf": round(bs, 3)}
            used_new.add(best)

    # ---- Pass 4: hierarchical absorb (restructuring chapter <-> subchapter) --
    # Unmapped OLD subchapters are attached to the record of their nearest mapped
    # ancestor (old_absorbed); unmapped NEW subchapters to the record of their mapped
    # new ancestor (new_absorbed). This makes the paragraph level compare "whole old
    # chapter" <-> "new chapter incl. new substructure", so restructurings do not
    # falsely appear as new/removed (point 6).
    mapped_old_ids = {olds[oi]["id"]: oi for oi in o2n}
    mapped_new_ids = {news[m["ni"]]["id"] for m in o2n.values()}
    old_absorb: dict[int, list[int]] = {}     # oi(ancestor) -> [oi children]
    new_absorb: dict[int, list[int]] = {}     # ni(ancestor) -> [nj children]

    def _ancestor(sec_id: str, id_set) -> str | None:
        parts = sec_id.split(".")
        for k in range(len(parts) - 1, 0, -1):
            cand = ".".join(parts[:k])
            if cand in id_set:
                return cand
        return None

    for oi, o in enumerate(olds):
        if oi in o2n:
            continue
        anc = _ancestor(o["id"], mapped_old_ids.keys())
        if anc is not None:
            old_absorb.setdefault(mapped_old_ids[anc], []).append(oi)

    n_id_to_idx = {s["id"]: j for j, s in enumerate(news)}
    ni_mapped = {m["ni"] for m in o2n.values()}
    for nj, n in enumerate(news):
        if nj in used_new:
            continue
        anc = _ancestor(n["id"], mapped_new_ids)
        if anc is not None:
            anc_ni = n_id_to_idx[anc]
            if anc_ni in ni_mapped:
                new_absorb.setdefault(anc_ni, []).append(nj)
                used_new.add(nj)

    absorbed_old = {oi for kids in old_absorb.values() for oi in kids}

    # Materialize the titles of absorbed OLD sections as synthetic paragraphs:
    # in the PDF e.g. term names (3.1.x) are headings, in the DOCX normal paragraphs --
    # without this step they are missing from the comparison and chapter 3 looks fully changed.
    from ...text.textnorm import n1 as _n1
    for kids in old_absorb.values():
        for oi in kids:
            sec = olds[oi]
            if not sec["title"].strip():
                continue
            synth_id = f"{sec['id']}.p0"
            if any(p["id"] == synth_id for p in sec["paragraphs"]):
                continue
            sec["paragraphs"].insert(0, {
                "id": synth_id, "n0": sec["title"], "n1": _n1(sec["title"]),
                "kind": "term", "term_no": sec["id"], "tc": {"ins": 0, "del": 0},
                "modality": "informativ", "modality_counts": {},
                "refs_internal": [], "refs_external": [], "values": [],
                "formulas": [], "figures": [], "synthetic_title": True})

    # ---- Records ----------------------------------------------------------------
    records = []
    for oi, o in enumerate(olds):
        if oi in absorbed_old:
            continue
        base = {"old_id": o["id"], "old_title": o["title"], "old_level": o["level"],
                "old_part": o["part"], "old_paras": len(o["paragraphs"])}
        if oi in o2n:
            m = o2n[oi]
            n = news[m["ni"]]
            rec = {**base, "new_id": n["id"], "new_title": n["title"],
                   "new_level": n["level"], "new_part": n["part"],
                   "new_paras": len(n["paragraphs"]),
                   "match_type": m["type"], "confidence": m["conf"],
                   "part_changed": o["part"] != n["part"]}
            kids_o = old_absorb.get(oi, [])
            kids_n = new_absorb.get(m["ni"], [])
            if kids_o:
                rec["old_ids"] = [o["id"]] + [olds[k]["id"] for k in kids_o]
                rec["restructured"] = "merge_up"      # old subchapters resolved
            if kids_n:
                rec["new_ids"] = [n["id"]] + [news[k]["id"] for k in kids_n]
                rec["restructured"] = ("split_down" if not kids_o else "both")
            records.append(rec)
        else:
            records.append({**base, "new_id": None, "match_type": "removed",
                            "confidence": 0.0, "part_changed": False})
    for nj, n in enumerate(news):
        if nj not in used_new:
            records.append({"old_id": None, "new_id": n["id"], "new_title": n["title"],
                            "new_level": n["level"], "new_part": n["part"],
                            "new_paras": len(n["paragraphs"]),
                            "match_type": "new", "confidence": 0.0, "part_changed": False})
    return records


def write_mapping(records: list[dict], out_path: str | Path, pair_label: str):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"pair": pair_label, "records": records},
                              ensure_ascii=False, indent=1), encoding="utf-8")
