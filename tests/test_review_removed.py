"""AP-11: the review list of removal reports.

AP-09 measured that a chapter mapping with an old paragraph surplus produces removals,
AP-10 measured that in 32 cases across three corpora the removed text still stands in the
new edition -- in a section the paragraph aligner never got to see. Each signal alone is
weak: the surplus correlates with 39-56 % of the reports but causes about 5 %, and the
fuzzy text search finds the text without knowing whether it stands where it belongs.
Together they make a short list worth reading.

Nothing here is suppressed, reclassified or reweighted: the change stream stays what it
was, the relocation measurement is added to it, and the list is a second artifact.

All documents are built in the test itself, so every number stays readable.
"""
from __future__ import annotations

import json

from normpare.stages.diff import build_synopse
from normpare.stages.review_removed import (
    MIN_CHARS,
    OLD_SURPLUS_MIN,
    REVIEW_REASONS,
    RELOCATION_TAU,
    review_entries,
    trigrams,
    write_review_removed,
)


# --- mini documents ---------------------------------------------------------------

def _para(pid: str, text: str, kind: str = "text") -> dict:
    return {"id": pid, "n0": text, "n1": text, "kind": kind, "modality": "informativ",
            "modality_counts": {}, "refs_internal": [], "refs_external": [], "values": []}


def _section(sid: str, title: str, paras: list[dict]) -> dict:
    return {"id": sid, "title": title, "level": 1, "part": "main",
            "paragraphs": paras, "tables": [], "figures": [], "formulas": []}


def _doc(doc_id: str, sections: list[dict]) -> dict:
    return {"doc_id": doc_id, "sections": sections,
            "source": {"format": "docx", "sha256": "0" * 8}}


def _record(old_ids: list[str], new_ids: list[str], links: list[dict],
            mapping_id: str | None = None) -> dict:
    rec = {"old_id": old_ids[0] if old_ids else None,
           "new_id": new_ids[0] if new_ids else None,
           "old_ids": old_ids, "new_ids": new_ids,
           "old_title": "Kapitel alt", "new_title": "Kapitel neu",
           "old_level": 1, "new_level": 1, "old_part": "main", "new_part": "main",
           "match_type": "id+title", "confidence": 1.0,
           "part_changed": False, "para_links": links}
    if mapping_id:
        rec["mapping_id"] = mapping_id
    return rec


def _synopse(tmp_path, old_doc, new_doc, records, name: str = "synopse.json") -> dict:
    return build_synopse(old_doc, new_doc, records, None, tmp_path / name, "alt <-> neu")


# --- texts ------------------------------------------------------------------------

#: The removed span. Long enough to be judged (>= MIN_CHARS).
GONE = ("Die Anlage nach Abschnitt 5 ist vor der Inbetriebnahme durch den "
        "Netzbetreiber zu pruefen.")
#: The same statement, reworded at the end -- the way a relocated span really looks
#: (AP-10 found no relocated case with a coverage of 1.00).
REWORDED = ("Die Anlage nach Abschnitt 5 ist vor der Inbetriebnahme durch den "
            "Netzbetreiber zu bewerten.")


def _filler_old(k: int) -> str:
    return f"Der Betreiber meldet die Kennwerte der Erzeugungseinheit Nummer {k}."


def _filler_new(k: int) -> str:
    return f"Der Betreiber uebermittelt die Kennwerte der Erzeugungseinheit Nummer {k}."


def _pairs(n: int, prefix_o: str = "a", prefix_n: str = "n") -> tuple[list, list, list]:
    """``n`` old and ``n`` new filler paragraphs, paired one to one."""
    olds = [_para(f"{prefix_o}.p{i}", _filler_old(i)) for i in range(n)]
    news = [_para(f"{prefix_n}.p{i}", _filler_new(i)) for i in range(n)]
    links = [{"old_ids": [f"{prefix_o}.p{i}"], "new_ids": [f"{prefix_n}.p{i}"],
              "kind": "similar", "confidence": 0.9} for i in range(n)]
    return olds, news, links


def _removed_link(pid: str) -> dict:
    return {"old_ids": [pid], "new_ids": [], "kind": "removed", "confidence": 0.0}


# --- the three reasons ------------------------------------------------------------

def test_relocated_text_enters_the_queue(tmp_path):
    """The text stands in a section outside ``new_ids`` -- the mapping is at fault."""
    olds, news, links = _pairs(3)
    olds.append(_para("a.gone", GONE))
    links.append(_removed_link("a.gone"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    entries = review_entries(syn)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["review_reasons"] == ["relocated_outside_mapping"]
    assert entry["relocation"]["best_section"] == "X"
    assert entry["relocation"]["in_mapping"] is False
    assert entry["relocation"]["best_score"] >= RELOCATION_TAU
    assert entry["old_ids"] == ["a.gone"]
    assert entry["old_text"] == GONE
    assert entry["chars"] == len(GONE)
    assert entry["old_surplus"] == 1


def test_missed_text_enters_with_its_own_reason(tmp_path):
    """The text stands in a section that *is* mapped -- the aligner is at fault."""
    olds, news, links = _pairs(3)
    olds.append(_para("a.gone", GONE))
    news.append(_para("n.here", REWORDED))
    links.append(_removed_link("a.gone"))
    links.append({"old_ids": [], "new_ids": ["n.here"], "kind": "new", "confidence": 0.0})
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    entries = review_entries(syn)
    assert len(entries) == 1
    assert entries[0]["review_reasons"] == ["missed_inside_mapping"]
    assert entries[0]["relocation"]["best_section"] == "N"
    assert entries[0]["relocation"]["in_mapping"] is True


def test_surplus_alone_no_longer_enters(tmp_path):
    """No text found anywhere, only an old surplus of five: not a finding (AP-12).

    Inverts ``test_surplus_alone_is_enough`` of AP-11. The surplus correlates with 39-56 %
    of all removals and causes about 5 % of them, so admitting on it alone turned the
    review list into a second copy of the change stream (95 of 111 entries on 4110).
    """
    olds, news, links = _pairs(15)
    for i in range(5):
        olds.append(_para(f"a.gone{i}", f"{GONE} Fassung {i}."))
        links.append(_removed_link(f"a.gone{i}"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    ch = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])["chapters"][0]

    assert ch["paragraph_balance"]["old_surplus"] == OLD_SURPLUS_MIN
    # deliberately *not* unbalanced: old_surplus and the AP-09 imbalance are two criteria
    assert ch["paragraph_balance"]["unbalanced"] is False
    # the five removals are still in the change stream, they are just not review findings
    assert [c["kind"] for c in ch["changes"]].count("removed") == 5
    for change in ch["changes"]:
        if change["kind"] == "removed":
            assert change["relocation"]["best_score"] < RELOCATION_TAU
    assert review_entries({"chapters": [ch]}) == []


def test_surplus_survives_as_a_reason(tmp_path):
    """Relocated *and* out of a surplus mapping: the surplus stays a reason (AP-12)."""
    olds, news, links = _pairs(15)
    for i in range(5):
        olds.append(_para(f"a.gone{i}", f"{GONE} Fassung {i}."))
        links.append(_removed_link(f"a.gone{i}"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    entries = review_entries(syn)
    assert len(entries) == 5
    for entry in entries:
        assert entry["review_reasons"] == ["relocated_outside_mapping", "unbalanced_mapping"]
        assert entry["old_surplus"] == OLD_SURPLUS_MIN


def _surplus_case(mapping_id: str, tag: str, surplus: int) -> tuple[dict, dict, dict]:
    """One relocated removal out of a mapping with a chosen old surplus.

    The surplus is made of old paragraphs the aligner never links: they raise ``n_old``
    without adding a removal, so the mapping carries exactly one review entry.
    """
    olds, news, links = _pairs(3, prefix_o=f"{tag}o", prefix_n=f"{tag}n")
    olds.append(_para(f"{tag}o.gone", GONE))
    links.append(_removed_link(f"{tag}o.gone"))
    olds += [_para(f"{tag}o.pad{i}", _filler_old(100 + i)) for i in range(surplus - 1)]
    return (_section(f"A{tag}", "Kapitel alt", olds),
            _section(f"N{tag}", "Kapitel neu", news),
            _record([f"A{tag}"], [f"N{tag}"], links, mapping_id=mapping_id))


def test_surplus_still_sorts(tmp_path):
    """Same reason, same coverage, different surplus: the surplus decides the order."""
    o_a, n_a, rec_a = _surplus_case("N1<A1", "a", surplus=1)
    o_b, n_b, rec_b = _surplus_case("N2<A2", "b", surplus=OLD_SURPLUS_MIN)
    old_doc = _doc("alt", [o_a, o_b])
    new_doc = _doc("neu", [n_a, n_b, _section("X", "Anderes", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [rec_a, rec_b])

    entries = review_entries(syn)
    assert len(entries) == 2
    assert entries[0]["relocation"]["best_score"] == entries[1]["relocation"]["best_score"]
    assert [e["old_surplus"] for e in entries] == [OLD_SURPLUS_MIN, 1]
    # by mapping_id alone N1<A1 would come first -- the surplus outranks it
    assert [e["mapping_id"] for e in entries] == ["N2<A2", "N1<A1"]
    assert entries[0]["review_reasons"] == ["relocated_outside_mapping", "unbalanced_mapping"]
    assert entries[1]["review_reasons"] == ["relocated_outside_mapping"]


def test_relocated_entries_are_unaffected(tmp_path):
    """Dropping the surplus criterion touches neither the number nor the order of the
    entries that carry textual evidence."""
    o_a, n_a, rec_a = _surplus_case("N1<A1", "a", surplus=1)
    o_b, n_b, rec_b = _surplus_case("N2<A2", "b", surplus=OLD_SURPLUS_MIN)
    # a third mapping whose removals rest on the surplus alone -- these must vanish
    olds, news, links = _pairs(15, prefix_o="co", prefix_n="cn")
    for i in range(OLD_SURPLUS_MIN):
        olds.append(_para(f"co.gone{i}", f"Der Netzbetreiber fordert den Nachweis "
                                        f"nach Abschnitt {i} binnen vier Wochen an."))
        links.append(_removed_link(f"co.gone{i}"))
    rec_c = _record(["Ac"], ["Nc"], links, mapping_id="N3<A3")
    old_doc = _doc("alt", [o_a, o_b, _section("Ac", "Kapitel alt", olds)])
    new_doc = _doc("neu", [n_a, n_b, _section("Nc", "Kapitel neu", news),
                           _section("X", "Anderes", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [rec_a, rec_b, rec_c])

    entries = review_entries(syn)
    assert [(e["mapping_id"], e["old_ids"]) for e in entries] == [
        ("N2<A2", ["bo.gone"]), ("N1<A1", ["ao.gone"])]
    assert all("relocated_outside_mapping" in e["review_reasons"] for e in entries)


def test_unremarkable_removals_stay_out(tmp_path):
    """A balanced mapping and no textual evidence: nothing to review."""
    olds, news, links = _pairs(6)
    olds.append(_para("a.gone", GONE))
    news.append(_para("n.fresh", "Fuer den Netzanschluss gilt kuenftig das Zertifikat "
                                 "des Herstellers als Nachweis."))
    links.append(_removed_link("a.gone"))
    links.append({"old_ids": [], "new_ids": ["n.fresh"], "kind": "new", "confidence": 0.0})
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news)])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    assert syn["chapters"][0]["paragraph_balance"]["old_surplus"] == 0
    assert [c["kind"] for c in syn["chapters"][0]["changes"]].count("removed") == 1
    assert review_entries(syn) == []


def test_several_reasons_are_all_kept(tmp_path):
    """Relocated *and* out of a surplus mapping: both reasons, in table order."""
    olds, news, links = _pairs(15)
    for i in range(5):
        olds.append(_para(f"a.gone{i}", f"{GONE} Fassung {i}."))
        links.append(_removed_link(f"a.gone{i}"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    entries = review_entries(syn)
    assert len(entries) == 5
    for entry in entries:
        assert entry["review_reasons"] == ["relocated_outside_mapping", "unbalanced_mapping"]
        assert entry["review_reasons"] == [r for r in REVIEW_REASONS
                                           if r in entry["review_reasons"]]


def test_short_removals_get_no_relocation_key(tmp_path):
    """Below MIN_CHARS nothing is judged -- and no empty key is written either."""
    short = "Die Anlage ist zu pruefen und ferner zu warten."
    assert len(short) < MIN_CHARS
    olds, news, links = _pairs(3)
    olds.append(_para("a.short", short))
    links.append(_removed_link("a.short"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", short)])])
    out = tmp_path / "synopse.json"
    syn = build_synopse(old_doc, new_doc, [_record(["A"], ["N"], links)], None,
                        out, "alt <-> neu")

    removed = [c for c in syn["chapters"][0]["changes"] if c["kind"] == "removed"]
    assert len(removed) == 1
    assert "relocation" not in removed[0]
    assert "relocation" not in out.read_text(encoding="utf-8")
    assert review_entries(syn) == []


# --- what must not change ---------------------------------------------------------

def test_the_change_stream_is_untouched(tmp_path):
    """Only keys are added: no removal disappears, none changes its kind."""
    olds, news, links = _pairs(4)
    olds.append(_para("a.gone", GONE))
    news.append(_para("n.fresh", "Der Nachweis der Kurzschlussfestigkeit ist dem "
                                 "Netzbetreiber vorzulegen."))
    links.append(_removed_link("a.gone"))
    links.append({"old_ids": [], "new_ids": ["n.fresh"], "kind": "new", "confidence": 0.0})
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", REWORDED)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])
    changes = syn["chapters"][0]["changes"]

    # the relocation really is there -- otherwise the comparison below proves nothing
    removed = [c for c in changes if c["kind"] == "removed"]
    assert [set(c) & {"relocation"} for c in removed] == [{"relocation"}]

    stream = [(c["kind"], tuple(c["old_ids"]), tuple(c["new_ids"]),
               c["old_text"], c["new_text"])
              for c in changes]
    assert stream == [
        *[("similar", (f"a.p{i}",), (f"n.p{i}",), _filler_old(i), _filler_new(i))
          for i in range(4)],
        ("removed", ("a.gone",), (), GONE, None),
        ("new", (), ("n.fresh",), None,
         "Der Nachweis der Kurzschlussfestigkeit ist dem Netzbetreiber vorzulegen."),
    ]
    # the added key is the only difference to a removal record before AP-11
    assert set(removed[0]) == {"kind", "confidence", "old_ids", "new_ids", "old_text",
                               "new_text", "modality", "kennwerte", "relocation"}


# --- the artifact -----------------------------------------------------------------

def _relocated_case(mapping_id: str, tag: str) -> tuple[dict, dict, dict]:
    """One mapping whose single removal is relocated into a foreign section."""
    olds, news, links = _pairs(3, prefix_o=f"{tag}o", prefix_n=f"{tag}n")
    olds.append(_para(f"{tag}o.gone", GONE))
    links.append(_removed_link(f"{tag}o.gone"))
    return (_section(f"A{tag}", "Kapitel alt", olds),
            _section(f"N{tag}", "Kapitel neu", news),
            _record([f"A{tag}"], [f"N{tag}"], links, mapping_id=mapping_id))


def test_order_is_total(tmp_path):
    """Equal score and equal surplus are still separated -- by mapping_id, then old_ids."""
    o_b, n_b, rec_b = _relocated_case("N2<A2", "b")
    o_a, n_a, rec_a = _relocated_case("N1<A1", "a")
    foreign = _section("X", "Anderes Kapitel", [_para("x.p0", REWORDED)])
    old_doc = _doc("alt", [o_b, o_a])
    new_doc = _doc("neu", [n_b, n_a, foreign])
    syn = _synopse(tmp_path, old_doc, new_doc, [rec_b, rec_a])

    entries = review_entries(syn)
    assert len(entries) == 2
    assert entries[0]["relocation"]["best_score"] == entries[1]["relocation"]["best_score"]
    assert {e["old_surplus"] for e in entries} == {1}
    # document order is b before a; the sort is by mapping_id, so a comes first
    assert [e["mapping_id"] for e in entries] == ["N1<A1", "N2<A2"]
    assert [e["old_ids"] for e in entries] == [["ao.gone"], ["bo.gone"]]


def test_queue_is_deterministic(tmp_path):
    o_b, n_b, rec_b = _relocated_case("N2<A2", "b")
    o_a, n_a, rec_a = _relocated_case("N1<A1", "a")
    old_doc = _doc("alt", [o_b, o_a])
    new_doc = _doc("neu", [n_b, n_a, _section("X", "Anderes", [_para("x.p0", REWORDED)])])
    records = [rec_b, rec_a]

    first, second = tmp_path / "one.json", tmp_path / "two.json"
    for name, target in (("syn_a.json", first), ("syn_b.json", second)):
        syn = build_synopse(old_doc, new_doc, json.loads(json.dumps(records)), None,
                            tmp_path / name, "alt <-> neu")
        write_review_removed(syn, target)
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 2


def test_trigram_overlap_is_order_sensitive(tmp_path):
    """Same words, different order: word trigrams see the difference, word sets do not."""
    shuffled = " ".join(reversed(GONE.split()))
    assert set(shuffled.lower().split()) == set(GONE.lower().split())
    assert len(trigrams(GONE) & trigrams(shuffled)) == 0

    olds, news, links = _pairs(3)
    olds.append(_para("a.gone", GONE))
    links.append(_removed_link("a.gone"))
    old_doc = _doc("alt", [_section("A", "Kapitel alt", olds)])
    new_doc = _doc("neu", [_section("N", "Kapitel neu", news),
                           _section("X", "Anderes Kapitel", [_para("x.p0", shuffled)])])
    syn = _synopse(tmp_path, old_doc, new_doc, [_record(["A"], ["N"], links)])

    removed = [c for c in syn["chapters"][0]["changes"] if c["kind"] == "removed"][0]
    assert removed["relocation"]["best_score"] < RELOCATION_TAU
    assert review_entries(syn) == []


def test_queue_is_complete_without_llm(tmp_path):
    """``--no-llm`` costs nothing and leaves nothing out: the list is deterministic."""
    from normpare.config import Config
    from normpare.pipeline import Pipeline

    o_a, n_a, rec_a = _relocated_case("N1<A1", "a")
    old_doc = _doc("alt", [o_a])
    new_doc = _doc("neu", [n_a, _section("X", "Anderes", [_para("x.p0", REWORDED)])])

    out = tmp_path / "run"
    (out / "alt").mkdir(parents=True)
    (out / "neu").mkdir(parents=True)
    (out / "alt" / "norm_doc.json").write_text(json.dumps(old_doc), encoding="utf-8")
    (out / "neu" / "norm_doc.json").write_text(json.dumps(new_doc), encoding="utf-8")
    (out / "mapping.json").write_text(
        json.dumps({"pair": "alt <-> neu", "records": [rec_a]}), encoding="utf-8")

    cfg = Config.for_compare("alt.docx", "neu.docx", out, pair_label="alt <-> neu")
    Pipeline(cfg).run(["synopse"], use_llm=False)

    assert not (out / "deutung.json").exists()
    payload = json.loads((out / "review_removed.json").read_text(encoding="utf-8"))
    assert [e["review_reasons"] for e in payload["entries"]] == \
        [["relocated_outside_mapping"]]
    syn = json.loads((out / "synopse.json").read_text(encoding="utf-8"))
    assert payload["entries"] == review_entries(syn)
