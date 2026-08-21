"""
align_paras.py -- stage S3b: paragraph alignment within mapped chapters
                  + document-wide post-passes moved / split / merged.

Per chapter pair:
  1  anchors: N2-identical paragraphs (unique; ambiguous ones by order)
  2  similarity: Hungarian over sim_backend, threshold tau (PDF source: tau-0.05)
  3  split: one old paragraph <-> 2-3 consecutive new (concatenation of similar ones)
     merge: 2-3 old <-> one new
  3b absorbed: a leftover old paragraph whose text is contained in an already paired
     new paragraph joins that link (n:1, kind "merged") -- containment, no threshold
  3c split: a leftover new paragraph whose text is contained in an already paired old
     paragraph joins that link (1:2, kind "split") -- the other half of a split paragraph
Document-wide:
  4  moved: remaining new x removed globally against each other (threshold tau_moved,
     from 40 characters, short pairs at TAU_MOVED_SHORT) -> instead of "new"+"removed",
     a moved link with source/target chapter, "via": "tfidf"
  5  block: a removal wedged between two paragraphs that moved into the same new chapter
     moved with them -- context, no similarity; "moved_to_chapter", "via": "block"
Optional (behind the embedding flag, default off):
     embed_rescue_pass  in-chapter re-pairing of residuals
     embed_move_pass    cross-chapter moves, only where all three checks hold
Result per chapter record: "para_links": [{old_ids, new_ids, kind, confidence}, ...]
kind in identical | similar | split | merged | new | removed  (+ document-wide moved)
"""
from __future__ import annotations
import numpy as np

from ...text.textnorm import compare_key


def _lsa(cost):
    """Optimal assignment (Hungarian). Thin lazy wrapper over SciPy so the heavy import
    stays out of module load and tests can substitute a lightweight solver."""
    from scipy.optimize import linear_sum_assignment
    return linear_sum_assignment(cost)

# kind 'formula' deliberately NOT in the text comparison: formulas go through the
# per-chapter formula diff (diffs.formulas_diff), not paragraph alignment
_COMPARE_KINDS = {"text", "list", "note", "term"}

# Link kinds a leftover old paragraph may be absorbed into (pass 3b). All of them pair
# exactly one new paragraph, so the absorbed text really lands in that paragraph.
_ABSORBING_KINDS = {"identical", "similar", "merged"}
#: Minimum compare_key length for an absorption. compare_key drops all spaces, so a
#: shorter span ("Allgemeines") is contained in many paragraphs by chance.
MIN_ABSORB_KEY = 20

#: Link kinds a leftover *new* paragraph may join (pass 3c). Exactly one old and one new
#: paragraph, so the pairing widens to 1:2 -- the shape of a split.
_SPLIT_HOST_KINDS = {"identical", "similar"}
#: Minimum compare_key length for a split half. Higher than MIN_ABSORB_KEY: the claim here
#: is that a whole new paragraph is a piece of an old one, and half a sentence is contained
#: in some paragraph of the chapter by chance.
MIN_SPLIT_KEY = 40

#: Shortest paragraph the document-wide ``moved`` pass looks at (AP-26 part B). Was 80,
#: which left 36 % (4110) / 39 % (4120) of all removals unexamined -- list items, formula
#: lines and short obligations, and exactly there sit the findings of the interpretation
#: work. The same value the embedding rescue pass has always used.
MOVED_MIN_LEN = 40

#: Below this length a pair counts as short and needs :data:`TAU_MOVED_SHORT` instead of
#: ``tau_moved``: short texts reach a moderate similarity by chance.
MOVED_SHORT_LEN = 80

#: Threshold for a short move. Measured, not set (AP-26, ``schwelle_kurz.txt``): the
#: assigned short candidate pairs have their largest gap just above 0.70 on all three
#: corpora -- 0.697 to 0.816 (4110), 0.682 to 0.856 (4120), 0.719 to 0.819 (60909) -- and
#: 0.80 is the one value inside all three. Above it every pair is the same sentence in a
#: new place; below it genuine and invented pairs mix. The same value AP-23 measured for
#: tables, on a different measure and a different object.
TAU_MOVED_SHORT = 0.80

#: Threshold for a cross-chapter embedding move (AP-26 part C). Higher than the 0.82 of
#: the in-chapter rescue pass, because the claim is stronger: not "this paragraph was
#: reworded here" but "this paragraph is now somewhere else". Measured, not set.
TAU_CROSS = 0.90


def _paras(sec) -> list[dict]:
    return [p for p in sec["paragraphs"] if p.get("n1", p.get("n0", "")).strip()
            and p.get("kind", "text") in _COMPARE_KINDS]


def _txt(p) -> str:
    return p.get("n1") or p.get("n0") or ""


def align_chapter(old_paras, new_paras, sim_backend, tau: float, stats: dict | None = None):
    links = []
    used_o, used_n = set(), set()

    # ---- 1) anchors: compare_key identity (cosmetics-invariant) --------------------
    from collections import defaultdict
    o_by_n2, n_by_n2 = defaultdict(list), defaultdict(list)
    for i, p in enumerate(old_paras):
        o_by_n2[compare_key(_txt(p))].append(i)
    for j, p in enumerate(new_paras):
        n_by_n2[compare_key(_txt(p))].append(j)
    for key, o_idx in o_by_n2.items():
        n_idx = n_by_n2.get(key)
        if not n_idx or not key:
            continue
        for a, b in zip(o_idx, n_idx):        # stable order for multiple occurrences
            links.append({"o": [a], "n": [b], "kind": "identical", "conf": 1.0})
            used_o.add(a)
            used_n.add(b)

    # ---- 2) similarity (Hungarian) ---------------------------------------------
    rem_o = [i for i in range(len(old_paras)) if i not in used_o]
    rem_n = [j for j in range(len(new_paras)) if j not in used_n]
    sim = None
    if rem_o and rem_n:
        sim = sim_backend.sim_matrix([_txt(old_paras[i]) for i in rem_o],
                                     [_txt(new_paras[j]) for j in rem_n])
        ri, cj = _lsa(-sim)
        for a, b in zip(ri, cj):
            s = float(sim[a, b])
            if s >= tau:
                links.append({"o": [rem_o[a]], "n": [rem_n[b]], "kind": "similar",
                              "conf": round(s, 3)})
                used_o.add(rem_o[a])
                used_n.add(rem_n[b])
        # post-pass for SHORT, heavily reworded paragraphs (interpretation feedback 10.4.5):
    # short sentences do not reach tau under heavy rewording although they are
    # content-wise pairs -- lowered threshold only for short-text pairs
        tau_short = max(tau - 0.15, 0.40)
        cand = sorted(((float(sim[a, b]), a, b) for a in range(len(rem_o))
                       for b in range(len(rem_n))), reverse=True)
        for s, a, b in cand:
            if s < tau_short:
                break
            i, j = rem_o[a], rem_n[b]
            if i in used_o or j in used_n:
                continue
            if min(len(_txt(old_paras[i])), len(_txt(new_paras[j]))) > 220:
                continue
            links.append({"o": [i], "n": [j], "kind": "similar", "conf": round(s, 3)})
            used_o.add(i)
            used_n.add(j)

    # ---- 3) split / merge (local, neighbor window, batched) -----------------------
    def _windows(free: list[int]) -> list[list[int]]:
        out = []
        for w in (2, 3):
            for st in range(len(free) - w + 1):
                grp = free[st:st + w]
                if grp[-1] - grp[0] == w - 1:      # consecutive in the document
                    out.append(grp)
        return out

    # split: one old paragraph <-> 2-3 consecutive new ones
    free_o = [i for i in range(len(old_paras)) if i not in used_o]
    wins = _windows([j for j in range(len(new_paras)) if j not in used_n])
    if free_o and wins:
        sm = sim_backend.sim_matrix([_txt(old_paras[i]) for i in free_o],
                                    [" ".join(_txt(new_paras[j]) for j in grp) for grp in wins])
        cand = sorted(((float(sm[a, b]), a, b) for a in range(len(free_o))
                       for b in range(len(wins))), reverse=True)
        for s, a, b in cand:
            if s < tau:
                break
            i, grp = free_o[a], wins[b]
            if i in used_o or any(j in used_n for j in grp):
                continue
            links.append({"o": [i], "n": grp, "kind": "split", "conf": round(s, 3)})
            used_o.add(i)
            used_n.update(grp)
    # merge: 2-3 consecutive old ones <-> one new
    free_n = [j for j in range(len(new_paras)) if j not in used_n]
    wins_o = _windows([i for i in range(len(old_paras)) if i not in used_o])
    if free_n and wins_o:
        sm = sim_backend.sim_matrix([" ".join(_txt(old_paras[i]) for i in grp) for grp in wins_o],
                                    [_txt(new_paras[j]) for j in free_n])
        cand = sorted(((float(sm[a, b]), a, b) for a in range(len(wins_o))
                       for b in range(len(free_n))), reverse=True)
        for s, a, b in cand:
            if s < tau:
                break
            grp, j = wins_o[a], free_n[b]
            if j in used_n or any(i in used_o for i in grp):
                continue
            links.append({"o": grp, "n": [j], "kind": "merged", "conf": round(s, 3)})
            used_o.update(grp)
            used_n.add(j)

    # ---- 3b) absorbed: an old paragraph taken up by an already paired new one ------
    # A 2:1 merge that pass 3 cannot see: its window needs two *free* neighbouring old
    # paragraphs, but the first one is already paired by similarity, so no window exists
    # and the second one falls through to "removed" -- a claim that a passage is gone
    # while it stands verbatim in the new edition. Recognised structurally, by
    # containment, never by lowering a threshold: the old text IS a part of the new one.
    paired = {l["n"][0]: l for l in links
              if len(l["n"]) == 1 and l["o"] and l["kind"] in _ABSORBING_KINDS}
    if paired:
        host_keys = {j: compare_key(_txt(new_paras[j])) for j in paired}
        for i in range(len(old_paras)):
            if i in used_o:
                continue
            key = compare_key(_txt(old_paras[i]))
            if len(key) < MIN_ABSORB_KEY:
                continue                   # too short: contained anywhere by chance
            host = next((j for j in sorted(paired)
                         if key != host_keys[j] and key in host_keys[j]), None)
            if host is None:
                continue
            link = paired[host]
            link["o"] = sorted(link["o"] + [i])
            link["kind"] = "merged"
            used_o.add(i)

    # ---- 3c) split: a new paragraph that is a piece of an already paired old one ----
    # The new edition splits an old paragraph in two. Pass 2 pairs the first half and the
    # second one falls through to "new" although it stands verbatim in the old text -- the
    # same sentence counted once as a deletion and once as an addition. Pass 3 cannot see
    # it: its window needs two *free* neighbouring new paragraphs, but the Hungarian
    # assignment has already taken the old paragraph. Recognised by containment of the
    # compare_key, never by lowering a threshold, and strictly additive: an existing
    # pairing widens to 1:2, none is broken up.
    hosts = {l["o"][0]: l for l in links
             if len(l["o"]) == 1 and len(l["n"]) == 1 and l["kind"] in _SPLIT_HOST_KINDS}
    if hosts:
        host_keys = {i: compare_key(_txt(old_paras[i])) for i in hosts}
        taken = set()
        for j in range(len(new_paras)):
            if j in used_n:
                continue
            key = compare_key(_txt(new_paras[j]))
            if len(key) < MIN_SPLIT_KEY:
                continue               # too short: a piece of any paragraph by chance
            # Every eligible old paragraph shares the whole fragment, so the shared part
            # cannot rank them -- the smaller paragraph id decides.
            host = next((i for i in sorted(hosts) if key in host_keys[i]), None)
            if host is None:
                continue
            if host in taken:          # one old paragraph absorbs at most one extra half
                if stats is not None:
                    stats["split_skipped"] = stats.get("split_skipped", 0) + 1
                continue
            link = hosts[host]
            link["n"] = sorted(link["n"] + [j])
            link["kind"] = "split"
            used_n.add(j)
            taken.add(host)
            if stats is not None:
                stats["split_absorbed"] = stats.get("split_absorbed", 0) + 1

    # ---- remainder --------------------------------------------------------------
    for j in range(len(new_paras)):
        if j not in used_n:
            links.append({"o": [], "n": [j], "kind": "new", "conf": 0.0})
    for i in range(len(old_paras)):
        if i not in used_o:
            links.append({"o": [i], "n": [], "kind": "removed", "conf": 0.0})

    # chronology of the new version (place removed at the old spot: after the last
    # new index of smaller old neighbors -- simplified order: new index,
    # removed at the end of their old order)
    def sort_key(l):
        if l["n"]:
            return (0, min(l["n"]))
        return (1, min(l["o"]) if l["o"] else 0)
    links.sort(key=sort_key)
    return links


def align_document(old_doc, new_doc, mapping_records, sim_backend,
                   tau: float = 0.62, tau_moved: float = 0.72,
                   tau_moved_short: float | None = None,
                   stats: dict | None = None) -> list[dict]:
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    n_secs = {s["id"]: s for s in new_doc["sections"]}
    pdf_involved = (old_doc["source"]["format"] == "pdf") != (new_doc["source"]["format"] == "pdf")
    tau_eff = tau - 0.05 if pdf_involved else tau

    for rec in mapping_records:
        mt = rec["match_type"]
        if mt in ("removed", "new"):
            continue
        old_ids = rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else [])
        new_ids = rec.get("new_ids") or ([rec["new_id"]] if rec.get("new_id") else [])
        olds, news = [], []
        o_index, n_index = [], []
        for sid in old_ids:
            for p in _paras(o_secs.get(sid, {"paragraphs": []})):
                olds.append(p)
                o_index.append(p["id"])
        for sid in new_ids:
            for p in _paras(n_secs.get(sid, {"paragraphs": []})):
                news.append(p)
                n_index.append(p["id"])
        links = align_chapter(olds, news, sim_backend, tau_eff, stats=stats)
        rec["para_links"] = [{"old_ids": [o_index[i] for i in l["o"]],
                              "new_ids": [n_index[j] for j in l["n"]],
                              "kind": l["kind"], "confidence": l["conf"]} for l in links]

    # ---- 4) moved: document-wide post-pass ---------------------------------------
    tau_short = TAU_MOVED_SHORT if tau_moved_short is None else tau_moved_short
    all_new, all_removed = [], []          # (rec_idx, link_idx, para_dict, sec_id)
    o_par_by_id = {p["id"]: p for s in old_doc["sections"] for p in s["paragraphs"]}
    n_par_by_id = {p["id"]: p for s in new_doc["sections"] for p in s["paragraphs"]}
    for ri, rec in enumerate(mapping_records):
        for li, l in enumerate(rec.get("para_links", [])):
            if l["kind"] == "new" and l["new_ids"]:
                p = n_par_by_id[l["new_ids"][0]]
                if len(_txt(p)) >= MOVED_MIN_LEN:
                    all_new.append((ri, li, p))
            elif l["kind"] == "removed" and l["old_ids"]:
                p = o_par_by_id[l["old_ids"][0]]
                if len(_txt(p)) >= MOVED_MIN_LEN:
                    all_removed.append((ri, li, p))
    # include entirely new / removed chapters
    extra_new, extra_removed = [], []
    for rec in mapping_records:
        if rec["match_type"] == "new":
            for p in _paras(n_secs.get(rec["new_id"], {"paragraphs": []})):
                if len(_txt(p)) >= MOVED_MIN_LEN:
                    extra_new.append((rec["new_id"], p))
        elif rec["match_type"] == "removed":
            for p in _paras(o_secs.get(rec["old_id"], {"paragraphs": []})):
                if len(_txt(p)) >= MOVED_MIN_LEN:
                    extra_removed.append((rec["old_id"], p))

    moved = []
    rem_list = all_removed + [(None, None, p) for _, p in extra_removed]
    new_list = all_new + [(None, None, p) for _, p in extra_new]
    if rem_list and new_list:
        sim = sim_backend.sim_matrix([_txt(p) for _, _, p in rem_list],
                                     [_txt(p) for _, _, p in new_list])
        ri_, cj_ = _lsa(-sim)
        for a, b in zip(ri_, cj_):
            s = float(sim[a, b])
            r_ri, r_li, r_p = rem_list[a]
            n_ri, n_li, n_p = new_list[b]
            # a pair of short paragraphs needs more than tau_moved: a handful of technical
            # words is similar to some other handful by chance, and a wrong move is worse
            # than a missing one -- it claims a requirement still exists
            short = min(len(_txt(r_p)), len(_txt(n_p))) < MOVED_SHORT_LEN
            if s < (tau_short if short else tau_moved):
                continue
            moved.append({"old_id": r_p["id"], "new_id": n_p["id"],
                          "confidence": round(s, 3), "via": "tfidf"})
            if r_ri is not None:
                mapping_records[r_ri]["para_links"][r_li]["kind"] = "moved_away"
                mapping_records[r_ri]["para_links"][r_li]["moved_to"] = n_p["id"]
                mapping_records[r_ri]["para_links"][r_li]["confidence"] = round(s, 3)
                mapping_records[r_ri]["para_links"][r_li]["via"] = "tfidf"
            if n_ri is not None:
                mapping_records[n_ri]["para_links"][n_li]["kind"] = "moved_in"
                mapping_records[n_ri]["para_links"][n_li]["moved_from"] = r_p["id"]
                mapping_records[n_ri]["para_links"][n_li]["confidence"] = round(s, 3)
                mapping_records[n_ri]["para_links"][n_li]["via"] = "tfidf"

    # ---- 5) block continuation: a removal wedged between two moves ----------------
    moved += block_continuation_pass(old_doc, new_doc, mapping_records)
    return moved


def _chapter_of(link: dict, chapter_of_new: dict) -> str | None:
    """The new chapter a link points to -- from its target paragraph or, for a block
    continuation, from the chapter it already carries."""
    if link.get("moved_to"):
        return chapter_of_new.get(link["moved_to"])
    return link.get("moved_to_chapter")


def _neighbour(order: list[dict], start: int, step: int) -> dict | None:
    """The nearest link that is not a removal, seen from ``start`` in direction ``step``."""
    k = start + step
    while 0 <= k < len(order) and order[k]["kind"] == "removed":
        k += step
    return order[k] if 0 <= k < len(order) else None


def block_continuation_pass(old_doc, new_doc, mapping_records) -> list[dict]:
    """AP-26 part A: a removed paragraph between two moves went with them.

    The argument is the context, not the text: if the nearest moved neighbour *before* and
    the nearest moved neighbour *after* a removed paragraph both went into the same new
    chapter, the paragraph between them went there too. That holds for short paragraphs,
    where every similarity measure fails, which is the point -- over a third of all
    removals never reach the similarity pass at all.

    Only removals may lie between: a paragraph that stayed in the old chapter breaks the
    block, because then the chapter plainly did not move as a unit. Order is the paragraph
    order of the *old* chapter, not the sorted link order.

    What is proven is the chapter, not the paragraph, so the link gets
    ``moved_to_chapter`` and deliberately never ``moved_to``. ``confidence`` stays at 0.0:
    no similarity was measured, and a number here would suggest one. Returns the filled
    positions for reporting.
    """
    o_secs = {s["id"]: s for s in old_doc["sections"]}
    chapter_of_new = {p["id"]: s["id"] for s in new_doc["sections"] for p in s["paragraphs"]}
    filled = []
    for rec in mapping_records:
        links = rec.get("para_links") or []
        if not links:
            continue
        link_of_old = {oid: l for l in links for oid in l["old_ids"]}
        old_ids = rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else [])
        order = [link_of_old.get(p["id"])
                 for sid in old_ids for p in _paras(o_secs.get(sid, {"paragraphs": []}))]
        order = [l for l in order if l is not None]

        for k, link in enumerate(order):
            if link["kind"] != "removed":
                continue
            before, after = _neighbour(order, k, -1), _neighbour(order, k, 1)
            if not before or not after:
                continue
            if before["kind"] != "moved_away" or after["kind"] != "moved_away":
                continue
            target = _chapter_of(before, chapter_of_new)
            if target is None or target != _chapter_of(after, chapter_of_new):
                continue
            link["kind"] = "moved_away"
            link["moved_to_chapter"] = target
            link["via"] = "block"
            filled.append({"old_id": link["old_ids"][0], "new_id": None,
                           "chapter": target, "confidence": 0.0, "via": "block"})
    return filled


def embed_rescue_pass(old_doc, new_doc, mapping_records, embed_backend,
                      tau_embed: float = 0.82, min_len: int = 40) -> list[dict]:
    """Optional cascade step (ADR-0001): re-pair residual ``removed x new`` units.

    The deterministic TF-IDF pass (:func:`align_document`) stays the default and resolves
    the bulk of the pairs. This pass runs *after* it, purely additively, and only
    **within the same chapter**: for every chapter it takes the paragraphs still labelled
    ``removed`` / ``new`` and looks for a semantic match with an embedding backend, turning a
    matched pair into a single ``similar`` (changed) link. This catches paragraphs that were
    reworded in place so heavily that TF-IDF missed them. Cross-chapter moves are deliberately
    *not* handled here: a global assignment over all residuals forces spurious pairings
    (any two technical paragraphs score moderately high), so moves are left to the
    deterministic ``moved`` pass. The deterministic result is only ever refined, never
    weakened, and the step is gated behind a config flag (default off). Returns the list of
    rescued pairs for reporting.
    """
    o_par = {p["id"]: p for s in old_doc["sections"] for p in s["paragraphs"]}
    n_par = {p["id"]: p for s in new_doc["sections"] for p in s["paragraphs"]}
    rescued = []
    for rec in mapping_records:
        links = rec.get("para_links") or []
        rem = [(li, o_par.get(l["old_ids"][0])) for li, l in enumerate(links)
               if l["kind"] == "removed" and l["old_ids"]]
        rem = [(li, p) for li, p in rem if p and len(_txt(p)) >= min_len]
        new = [(li, n_par.get(l["new_ids"][0])) for li, l in enumerate(links)
               if l["kind"] == "new" and l["new_ids"]]
        new = [(li, p) for li, p in new if p and len(_txt(p)) >= min_len]
        if not rem or not new:
            continue
        sim = embed_backend.sim_matrix([_txt(p) for _, p in rem], [_txt(p) for _, p in new])
        ri_, cj_ = _lsa(-sim)
        for a, b in zip(ri_, cj_):
            s = float(sim[a, b])
            if s < tau_embed:
                continue
            r_li, r_p = rem[a]
            n_li, n_p = new[b]
            links[r_li]["kind"] = "similar"
            links[r_li]["new_ids"] = [n_p["id"]]
            links[r_li]["confidence"] = round(s, 3)
            links[r_li]["via"] = "embed"
            links[n_li]["kind"] = "_drop"
            rescued.append({"old_id": r_p["id"], "new_id": n_p["id"],
                            "confidence": round(s, 3), "same_chapter": True})
        rec["para_links"] = [l for l in links if l.get("kind") != "_drop"]
    return rescued


def values_are_consistent(old_text: str, new_text: str) -> bool:
    """Does every parameter value of the old side stand unchanged on the new one?

    The check rule behind a cross-chapter move (AP-26 part C, condition 2). A move claims
    that the content is unchanged and only stands elsewhere. If a limit is missing on the
    new side or reads differently there, that claim is wrong -- the requirement was
    rewritten, not relocated, and calling it a move would hide the very change a synopsis
    exists for. Values *added* on the new side do not block: they extend the statement,
    they do not remove the old one. A paragraph without values passes trivially.
    """
    from ..enrich.values import diff_values

    d = diff_values(old_text or "", new_text or "")
    return not d["changed"] and not d["removed"]


def embed_move_pass(old_doc, new_doc, mapping_records, embed_backend,
                    tau_cross: float | None = None, min_len: int = MOVED_MIN_LEN) -> list[dict]:
    """AP-26 part C: cross-chapter moves from embeddings -- but only with three proofs.

    Runs after :func:`embed_rescue_pass`, behind the same flag (default off), over the
    residual ``removed`` / ``new`` links of the whole document. The docstring of the rescue
    pass names the reason this did not exist: a global assignment over all residuals forces
    spurious pairings, because any two technical paragraphs score moderately high. The
    similarity alone therefore does not decide. A pair becomes a move only if

    1. its similarity reaches ``tau_cross`` (higher than the in-chapter ``tau_embed``),
    2. every parameter value of the old side reappears unchanged on the new one
       (:func:`values_are_consistent`), and
    3. the pair is the best candidate for *both* sides, not merely the result of the
       assignment -- the guard against the "moderately high" cloud.

    A pair that clears (1) but fails (2) or (3) is **not** a move. It stays ``removed`` plus
    ``new`` and carries ``possible_move_to`` at the old place: the observation, the reason
    it was not accepted, and nothing that is not proven. Those cases enter the AP-11 review
    list under the reason ``possible_move``.

    Without a backend the pass is skipped and the records stay untouched, so a run without
    ``sentence-transformers`` loses this step and nothing else. Returns the accepted moves.
    """
    if embed_backend is None:
        return []
    tau = TAU_CROSS if tau_cross is None else tau_cross
    o_par = {p["id"]: p for s in old_doc["sections"] for p in s["paragraphs"]}
    n_par = {p["id"]: p for s in new_doc["sections"] for p in s["paragraphs"]}
    chapter_of_new = {p["id"]: s["id"] for s in new_doc["sections"] for p in s["paragraphs"]}

    rem, new = [], []                       # (record, link, paragraph), document order
    for rec in mapping_records:
        for l in rec.get("para_links") or []:
            if l["kind"] == "removed" and l["old_ids"]:
                p = o_par.get(l["old_ids"][0])
                if p and len(_txt(p)) >= min_len:
                    rem.append((rec, l, p))
            elif l["kind"] == "new" and l["new_ids"]:
                p = n_par.get(l["new_ids"][0])
                if p and len(_txt(p)) >= min_len:
                    new.append((rec, l, p))
    if not rem or not new:
        return []

    sim = embed_backend.sim_matrix([_txt(p) for _, _, p in rem],
                                   [_txt(p) for _, _, p in new])
    best_row = sim.argmax(axis=1)           # the favourite of every old paragraph
    best_col = sim.argmax(axis=0)           # the favourite of every new paragraph
    moves = []
    ri_, cj_ = _lsa(-sim)
    for a, b in zip(ri_, cj_):
        s = float(sim[a, b])
        if s < tau:
            continue
        _, r_link, r_p = rem[a]
        _, n_link, n_p = new[b]
        failed = []
        if not values_are_consistent(_txt(r_p), _txt(n_p)):
            failed.append("kennwerte")
        if best_row[a] != b or best_col[b] != a:
            failed.append("wechselseitig")
        if failed:
            r_link["possible_move_to"] = {"new_id": n_p["id"],
                                          "chapter": chapter_of_new.get(n_p["id"]),
                                          "similarity": round(s, 3), "failed": failed}
            continue
        r_link["kind"] = "moved_away"
        r_link["moved_to"] = n_p["id"]
        r_link["moved_to_chapter"] = chapter_of_new.get(n_p["id"])
        r_link["confidence"] = round(s, 3)
        r_link["via"] = "embed"
        n_link["kind"] = "moved_in"
        n_link["moved_from"] = r_p["id"]
        n_link["confidence"] = round(s, 3)
        n_link["via"] = "embed"
        moves.append({"old_id": r_p["id"], "new_id": n_p["id"],
                      "chapter": chapter_of_new.get(n_p["id"]),
                      "confidence": round(s, 3), "via": "embed"})
    return moves
