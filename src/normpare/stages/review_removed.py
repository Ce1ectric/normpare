"""AP-11: the review list of removal reports -- deterministic, without an LLM.

Two measurements exist for a ``removed`` report, and each is weak on its own:

* ``paragraph_balance.old_surplus`` (AP-09) says a chapter mapping pairs more old
  paragraphs than it has new ones, so the assignment *has* to report the surplus as
  removed. It correlates with 39-56 % of all reports but causes about 5 % -- most of the
  correlation is a size effect.
* the trigram search (AP-10) finds the removed text again in the new edition, but knowing
  the text exists says nothing about whether the aligner could ever have seen it.

Together they are a short, dependable list: the search says the text is still there, the
mapping says where the aligner was allowed to look, and the surplus says the mapping was
overloaded to begin with. AP-10 measured 32 provably wrong reports across three corpora
this way, none of them with a coverage of 1.00 -- every relocated span was reworded on
the way, which is exactly why the 15-character prefix rule of ``tools/removed_audit.py``
never found them.

Nothing is suppressed, reclassified or reweighted here. The change stream stays what it
was; :func:`annotate_relocation` adds one key to judged removals, and
:func:`write_review_removed` writes a second artifact beside ``synopse.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..text.textnorm import n3

#: A removal shorter than this many characters of ``old_text`` is not judged: a handful
#: of words covers itself by chance somewhere in a document of a quarter million
#: characters. Below it the ``relocation`` key is absent entirely -- a ``null`` on every
#: short removal would add thousands of empty fields to every synopsis.
MIN_CHARS = 60

#: Coverage from which a find counts as the same text. Steers the *classification*, not
#: the admission to the list: AP-10 swept 0.5 to 0.9 and found no knee, so the value is a
#: reading aid, not a filter.
RELOCATION_TAU = 0.7

#: Old paragraph surplus from which a mapping is loud enough to be worth a look (AP-09
#: measured it as the sharpest of the balance criteria).
OLD_SURPLUS_MIN = 5

#: The reasons for taking a removal into the list, in the order they are reported. A
#: removal may carry several; they are all kept, because they say different things.
REVIEW_REASONS = ("relocated_outside_mapping", "missed_inside_mapping",
                  "unbalanced_mapping")


# -- coverage over word trigrams -------------------------------------------------------

def trigrams(text: str) -> set[tuple[str, ...]]:
    """Word trigrams of the N3 form of ``text``.

    Word *sets* are deliberately not used: they measure vocabulary instead of textual
    identity. Below three words the whole word sequence is the single "trigram", so a
    short span still matches itself instead of matching nothing.
    """
    words = n3(text).split()
    if len(words) < 3:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def _candidates(new_doc: dict) -> list[dict]:
    """Every paragraph of the new edition the aligner would consider, in document order."""
    # local import: keeps this module free of the numpy import that align.paras carries
    from .align.paras import _paras

    out = []
    for sec in new_doc.get("sections") or []:
        for p in _paras(sec):
            out.append({"section": sec.get("id"),
                        "trigrams": trigrams(p.get("n1") or p.get("n0") or "")})
    return out


def _best(needle: set[tuple[str, ...]], cands: list[dict]) -> tuple[float, dict | None]:
    """Best covering candidate paragraph, ``|T(old) & T(new)| / |T(old)|``.

    Ties fall to the first candidate in document order, so the result never depends on
    iteration order.
    """
    best, hit = 0.0, None
    if not needle:
        return best, hit
    for cand in cands:
        score = len(needle & cand["trigrams"]) / len(needle)
        if score > best:
            best, hit = score, cand
    return round(best, 4), hit


def annotate_relocation(chapters: list[dict], new_doc: dict) -> None:
    """Add ``relocation`` to every judged ``removed`` record of ``chapters``, in place.

    Judged means at least :data:`MIN_CHARS` characters of ``old_text``; below that the
    key stays absent. Purely additive -- no existing field is read out of the record
    other than its text, and none is changed.
    """
    cands = _candidates(new_doc)
    for ch in chapters:
        new_ids = set(ch.get("new_ids") or
                      ([ch["new_id"]] if ch.get("new_id") else []))
        for change in ch.get("changes") or []:
            text = change.get("old_text") or ""
            if change.get("kind") != "removed" or len(text) < MIN_CHARS:
                continue
            score, hit = _best(trigrams(text), cands)
            change["relocation"] = {
                "best_score": score,
                "best_section": hit["section"] if hit else None,
                "in_mapping": bool(hit and hit["section"] in new_ids),
            }


# -- the review list -------------------------------------------------------------------

def _reasons(change: dict, old_surplus: int) -> list[str]:
    """Why this removal is worth a look -- all applicable reasons, in table order."""
    reloc = change.get("relocation") or {}
    found = reloc.get("best_score", 0.0) >= RELOCATION_TAU
    holds = {
        "relocated_outside_mapping": found and not reloc.get("in_mapping"),
        "missed_inside_mapping": found and bool(reloc.get("in_mapping")),
        "unbalanced_mapping": old_surplus >= OLD_SURPLUS_MIN,
    }
    return [r for r in REVIEW_REASONS if holds[r]]


def _sort_key(entry: dict) -> tuple:
    """Total order: reason, then coverage, then surplus, then the two identifiers.

    The last two make the order total -- two removals out of the same mapping with the
    same coverage would otherwise depend on dict order.
    """
    reasons = entry["review_reasons"]
    rank = (0 if "relocated_outside_mapping" in reasons
            else 1 if "missed_inside_mapping" in reasons else 2)
    return (rank,
            -(entry.get("relocation") or {}).get("best_score", 0.0),
            -entry["old_surplus"],
            str(entry["mapping_id"] or ""),
            [str(i) for i in entry["old_ids"]])


def review_entries(synopse: dict) -> list[dict]:
    """Every judged ``removed`` report with at least one reason, in review order."""
    entries = []
    for ch in synopse.get("chapters") or []:
        old_surplus = (ch.get("paragraph_balance") or {}).get("old_surplus") or 0
        for change in ch.get("changes") or []:
            if change.get("kind") != "removed" or "relocation" not in change:
                continue
            reasons = _reasons(change, old_surplus)
            if not reasons:
                continue
            entries.append({
                "mapping_id": ch.get("mapping_id") or ch.get("new_id") or ch.get("old_id"),
                "old_ids": change.get("old_ids") or [],
                "title": ch.get("title"),
                "old_text": change.get("old_text"),
                "chars": len(change.get("old_text") or ""),
                "review_reasons": reasons,
                "relocation": change["relocation"],
                "old_surplus": old_surplus,
            })
    return sorted(entries, key=_sort_key)


def write_review_removed(synopse: dict, out_path: str | Path) -> dict:
    """Write ``review_removed.json`` and return its payload.

    Deterministic and free of any LLM: with ``--no-llm`` the list is complete, which is
    the point of building it out of measurements instead of interpretations.
    """
    entries = review_entries(synopse)
    payload = {
        "pair": synopse.get("pair"),
        "thresholds": {"relocation_tau": RELOCATION_TAU,
                       "old_surplus_min": OLD_SURPLUS_MIN,
                       "min_chars": MIN_CHARS},
        "counts": {"entries": len(entries),
                   **{r: sum(1 for e in entries if r in e["review_reasons"])
                      for r in REVIEW_REASONS}},
        "entries": entries,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload
