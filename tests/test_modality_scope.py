"""Modality measured where it changes: over the changed sentence pairs (AP-25).

The paragraph maximum hides every shift that happens next to a stronger sentence -- a
paragraph carrying one "muss" reports "muss" whatever its other sentences do, so
``sollte -> muss`` in a second sentence disappears. These tests pin the sentence level and
the second half of AP-25: a swap inside the same deontic class is no shift.

No network, no LLM, no real standard text.
"""
from normpare.stages.diff import _para_change, sentence_modality
from normpare.stages.enrich.modality import shift


def _paras(prefix: str, *texts: str) -> list[dict]:
    """Paragraphs the way the change record sees them (enriched, ``n1`` + paragraph max)."""
    from normpare.stages.enrich.modality import classify_paragraph
    return [{"id": f"{prefix}.p{i}", "kind": "text", "n0": t, "n1": t,
             "modality": classify_paragraph(t)["max"], "modality_counts": {},
             "refs_internal": [], "refs_external": [], "values": []}
            for i, t in enumerate(texts)]


# -- part 1: the sentence, not the paragraph -----------------------------------------------

def test_a_shift_in_one_sentence_is_not_masked():
    """A paragraph with a standing "muss" must not swallow ``sollte -> muss`` next to it.

    4110 4.2.1/0, the case that started AP-25: reported as "muss -> muss (unveraendert)"
    because both paragraphs carry a "muss" somewhere.
    """
    old = ("Die Anlage muss geerdet werden. "
           "Prinzipiell sollte die Planung frühzeitig erfolgen.")
    new = ("Die Anlage muss geerdet werden. "
           "Die Planung muss frühzeitig erfolgen.")
    m = sentence_modality(old, new)
    assert m["shift"] == "verschaerft"
    assert (m["old"], m["new"]) == ("sollte", "muss")


def test_an_unchanged_sentence_does_not_contribute():
    """A sentence nobody touched carries no shift, however binding it is."""
    old = "Die Anlage muss geerdet werden. Die Spannung beträgt 20 kV."
    new = "Die Anlage muss geerdet werden. Die Spannung beträgt 30 kV."
    m = sentence_modality(old, new)
    assert m["shift"] == "unveraendert"
    assert [s for s in m["sentence_shifts"]
            if s["shift"] in ("verschaerft", "gelockert")] == []
    # without a shift the summary keeps saying what the two paragraphs carry
    assert (m["old"], m["new"]) == ("muss", "muss")


def test_the_strongest_shift_wins():
    """Two shifts in one change: the larger distance on ``RANK`` decides."""
    old = ("Der Betreiber kann die Messung wiederholen. "
           "Die Unterlagen sollten aufbewahrt werden.")
    new = ("Der Betreiber sollte die Messung wiederholen. "
           "Die Unterlagen müssen aufbewahrt werden.")
    m = sentence_modality(old, new)
    # kann -> sollte spans two ranks, sollte -> muss only one
    assert (m["old"], m["new"], m["shift"]) == ("kann", "sollte", "verschaerft")


def test_sentence_shifts_are_recorded():
    """``sentence_shifts`` shows what the summary summarises -- both pairs, with text."""
    old = ("Der Betreiber kann die Messung wiederholen. "
           "Die Unterlagen sollten aufbewahrt werden.")
    new = ("Der Betreiber sollte die Messung wiederholen. "
           "Die Unterlagen müssen aufbewahrt werden.")
    shifts = sentence_modality(old, new)["sentence_shifts"]
    assert [(s["old"], s["new"], s["shift"]) for s in shifts] == [
        ("kann", "sollte", "verschaerft"),
        ("sollte", "muss", "verschaerft")]
    assert "Messung" in shifts[0]["sentence"]
    assert "Unterlagen" in shifts[1]["sentence"]


def test_a_paragraph_without_changes_stays_unchanged():
    """No changed sentence, no shift -- as before."""
    text = "Die Anlage muss geerdet werden. Der Betreiber kann eine Prüfung verlangen."
    m = sentence_modality(text, text)
    assert m["shift"] == "unveraendert"
    assert m["sentence_shifts"] == []


def test_added_and_removed_sentences_are_counted():
    """A sentence without a partner is an addition or a loss, not a shift."""
    old = ("Die Anlage muss geerdet werden. "
           "Die alte Regelung entfällt vollständig ersatzlos.")
    new = ("Die Anlage muss geerdet werden. "
           "Der Netzbetreiber veröffentlicht die Anschlussbedingungen jährlich.")
    m = sentence_modality(old, new)
    kinds = {s["shift"] for s in m["sentence_shifts"]}
    assert kinds == {"hinzugefuegt", "entfallen"}
    added = [s for s in m["sentence_shifts"] if s["shift"] == "hinzugefuegt"]
    removed = [s for s in m["sentence_shifts"] if s["shift"] == "entfallen"]
    assert len(added) == len(removed) == 1
    assert added[0]["old"] is None and "Netzbetreiber" in added[0]["sentence"]
    assert removed[0]["new"] is None and "Regelung" in removed[0]["sentence"]
    # an unpaired sentence never invents a shift
    assert m["shift"] == "unveraendert"


def test_modality_is_deterministic():
    """Same input twice -- same order, same values; ties broken by position in the text."""
    old = ("Der Betreiber kann die Messung wiederholen. "
           "Der Betreiber kann die Prüfung wiederholen.")
    new = ("Der Betreiber muss die Messung wiederholen. "
           "Der Betreiber muss die Prüfung wiederholen.")
    first, second = sentence_modality(old, new), sentence_modality(old, new)
    assert first == second
    assert first["shift"] == "verschaerft"
    # both pairs shift by the same distance: the first one in the text wins
    assert "Messung" in first["sentence_shifts"][0]["sentence"]


# -- part 2: a swap inside the same deontic class --------------------------------------------

def test_kann_to_darf_is_no_shift():
    """``kann`` and ``darf`` are both permissions -- the new edition only rewords."""
    assert shift("kann", "darf") == "unveraendert"
    assert shift("darf", "kann") == "unveraendert"
    old = "Die Werte können nur bei Volllast bewertet werden."
    new = "Die Werte dürfen nur bei Volllast bewertet werden."
    m = sentence_modality(old, new)
    assert m["shift"] == "unveraendert"
    assert [s for s in m["sentence_shifts"]
            if s["shift"] in ("verschaerft", "gelockert")] == []


def test_muss_to_darf_is_still_a_shift():
    """Different classes keep their shift -- the rule must not swallow real movement."""
    assert shift("muss", "darf") == "gelockert"
    assert shift("darf", "muss") == "verschaerft"
    assert shift("darf", "darf_nicht") == "verschaerft"
    assert shift("kann", "muss") == "verschaerft"
    assert shift("sollte", "muss") == "verschaerft"


# -- the change record ----------------------------------------------------------------------

def test_the_change_record_carries_the_sentence_shifts():
    """``_para_change`` reads the sentences, not the paragraph maxima."""
    old = ("Die Anlage muss geerdet werden. "
           "Prinzipiell sollte die Planung frühzeitig erfolgen.")
    new = ("Die Anlage muss geerdet werden. "
           "Die Planung muss frühzeitig erfolgen.")
    rec = _para_change("similar", _paras("a", old), _paras("n", new), 0.9)
    assert rec["modality"]["shift"] == "verschaerft"
    assert rec["modality"]["old"] == "sollte"
    assert [s["shift"] for s in rec["modality"]["sentence_shifts"]] == ["verschaerft"]


def test_a_one_sided_record_keeps_the_paragraph_maximum():
    """New and removed records have no pair to measure -- unchanged behaviour."""
    rec = _para_change("new", [], _paras("n", "Die Anlage muss geerdet werden."), 0.0)
    assert rec["modality"] == {"new": "muss"}
