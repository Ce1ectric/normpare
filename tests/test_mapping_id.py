"""The stable identifier of a chapter mapping (AP-06, ENT-24).

``cid = new_id or old_id`` names a chapter, but it does not identify a *mapping*: it
drops one side of every merge and split, and it collides as soon as ingest hands out
the same synthetic id twice. ``mapping_id`` carries both sides, sorted, and is unique
by construction:

    4.3<4.2      new 4.3 comes from old 4.2
    1<1          unchanged
    6<6.1+6.2    merge
    4+4.2<4      split
    10<          added
    <11          removed

These tests use the synthetic corpus and hand-built documents only -- no standard text,
no network.
"""
from __future__ import annotations

from normpare.stages.align.sections import build_section_mapping, mapping_id


def _section(sid: str, title: str, text: str, level: int = 1, part: str = "hauptteil") -> dict:
    return {"id": sid, "title": title, "level": level, "part": part,
            "paragraphs": [{"id": f"{sid}.p1", "n0": text, "n1": text.lower()}]}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "title": doc_id, "sections": sections}


# -- 1-3, 8: how the identifier is built ------------------------------------------------

def test_one_to_one_id():
    """A renumbered chapter names both of its numbers."""
    assert mapping_id(["4.2"], ["4.3"]) == "4.3<4.2"
    assert mapping_id(["1"], ["1"]) == "1<1"


def test_added_and_removed_ids():
    """A missing side stays empty -- the separator alone says which side it was."""
    assert mapping_id([], ["10"]) == "10<"
    assert mapping_id(["11"], []) == "<11"


def test_merge_id_sorts_sources():
    """Both sides are sorted, so the same mapping yields the same id in any input order."""
    assert mapping_id(["6.1", "6.2"], ["6"]) == "6<6.1+6.2"
    assert mapping_id(["6.2", "6.1"], ["6"]) == "6<6.1+6.2"
    assert mapping_id(["4"], ["4.2", "4"]) == "4+4.2<4"


def test_long_source_list_is_shortened_deterministically():
    """Beyond three sources the list is cut and a digest of the full list is appended."""
    ids = [f"7.{i}" for i in range(1, 8)]
    short = mapping_id(ids, ["7"])
    assert short.startswith("7<7.1+7.2+7.3+~")
    assert len(short.rsplit("~", 1)[1]) == 8
    assert short == mapping_id(list(reversed(ids)), ["7"])          # twice, same id
    # the digest covers the whole list, so a different tail is a different id
    assert short != mapping_id(ids[:-1] + ["7.9"], ["7"])


# -- 4, 5, 7: over a real (synthetic) run -----------------------------------------------

def test_ids_are_unique_within_a_run(synthetic_run):
    """No two mappings of one run share an id -- in the mapping and in the synopsis."""
    for name, records in (("mapping.json", synthetic_run.mapping["records"]),
                          ("synopse.json", synthetic_run.synopse["chapters"])):
        ids = [r["mapping_id"] for r in records]
        assert len(ids) == len(set(ids)), f"{name}: duplicate mapping_id"
        assert all(ids), f"{name}: a record without a mapping_id"


def test_id_is_stable_across_runs(synthetic_run):
    """Recomputing the id from the stored id lists reproduces the stored id exactly."""
    for rec in synthetic_run.mapping["records"]:
        old_ids = rec.get("old_ids") or ([rec["old_id"]] if rec.get("old_id") else [])
        new_ids = rec.get("new_ids") or ([rec["new_id"]] if rec.get("new_id") else [])
        again = mapping_id(old_ids, new_ids)
        assert again == rec["mapping_id"] == mapping_id(old_ids, new_ids)


def test_cid_remains_derivable(synthetic_run):
    """``cid`` is not replaced: both id fields survive and still yield the old key."""
    for ch in synthetic_run.synopse["chapters"]:
        cid = ch.get("new_id") or ch.get("old_id")
        assert cid, "a chapter without any id"
        assert cid in ch["mapping_id"]


# -- 6: the failure AP-01 found in the real corpus --------------------------------------

def test_duplicate_synthetic_ids_get_distinct_mapping_ids():
    """Two chapters carrying the same synthetic id are still told apart.

    ``anhang_informativ`` was handed out three times in the 4110 run; every consumer
    that keys on it loses two of the three chapters.
    """
    from normpare.text.similarity import load_backend

    sections = [_section("anhang_informativ", "Anhang A — Beispiele",
                         "Der Anhang zeigt Beispiele fuer die Auslegung der Anlage."),
                _section("anhang_informativ", "Anhang B — Formulare",
                         "Der Anhang enthaelt die Formulare fuer die Inbetriebsetzung.")]
    old = _doc("old", [dict(s, paragraphs=[dict(p) for p in s["paragraphs"]])
                       for s in sections])
    new = _doc("new", [dict(s, paragraphs=[dict(p) for p in s["paragraphs"]])
                       for s in sections])

    records = build_section_mapping(old, new, load_backend("tfidf"))
    ids = [r["mapping_id"] for r in records]
    assert len(records) == 2
    assert len(set(ids)) == 2, f"the duplicate id collapsed both mappings: {ids}"
    assert all("anhang_informativ" in i for i in ids)
