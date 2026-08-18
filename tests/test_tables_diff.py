"""Tests for normpare.stages.diff.tables_diff (table pairing old<->new).

AP-22: the pairing used to run greedily over the new tables in document order, compare
the *full* caption including its number, and consult the cells only when a caption was
missing. In 4110/10.3.4 that paired the old table 11 with the new table 16 and reported
the new table 17 as an addition and the old table 10 as a deletion -- an invented set of
value changes, while the one real change of the chapter appeared nowhere.

What the tests below pin down:

* a string only counts as a caption when it looks like one (keyword, number, separator,
  some text) -- "Tabelle 10 empfohlen." is a sentence end, not a caption,
* the number never drives the pairing: only the descriptive rest is compared,
* caption and content are both measured and the stronger one decides,
* the assignment is global (Hungarian), so the order of the new tables cannot change it,
* the threshold applies after the assignment, so a weak pair falls apart into
  removed + new.

The material is synthetic and shaped like 10.3.4, no norm text. No network.
"""
from normpare.stages.diff import caption_text, tables_diff

# -- material, shaped like 4110/10.3.4 -------------------------------------------------------
# Old A ("Tabelle 11") belongs to new Y ("Tabelle 17") -- same descriptive caption.
# Old B (caption destroyed by the extraction) belongs to new X ("Tabelle 16") -- only the
# cells say so. Measured scores: A/X 0.836, A/Y 1.000, B/X 0.815, B/Y 0.198.

TAB_A = {"id": "o_A", "caption": "Tabelle 11 - Empfohlene Grenzwerte fuer den Schutz an "
                                 "der Einheit",
         "cells": [["Kenngroesse", "Einstellwert"], ["U <", "0,80 Un"], ["f >", "51,5 Hz"]],
         "n_rows": 3, "n_cols": 2}
TAB_B = {"id": "o_B", "caption": "Tabelle 10 empfohlen.",
         "cells": [["Nachweis", "Unterlage"], ["Konformitaetserklaerung", "Anlage 3"],
                   ["Pruefbericht", "Anlage 4"]],
         "n_rows": 3, "n_cols": 2}
TAB_X = {"id": "n_X", "caption": "Tabelle 16 - Empfohlene Grenzwerte fuer den Schutz einer "
                                 "Anlage am Netz",
         "cells": [["Nachweis", "Unterlage"], ["Konformitaetserklaerung", "Anlage 3"],
                   ["Messprotokoll und Datenblatt", "Anlage 7"]],
         "n_rows": 3, "n_cols": 2}
TAB_Y = {"id": "n_Y", "caption": "Tabelle 17 - Empfohlene Grenzwerte fuer den Schutz an "
                                 "der Einheit",
         "cells": [["Kenngroesse", "Einstellwert"], ["U <", "0,80 Un"], ["f >", "52,5 Hz"]],
         "n_rows": 3, "n_cols": 2}


def _sec(tables):
    return [{"tables": tables}]


def _pairs(out):
    """The result as a comparable set, independent of the record order."""
    return {(r["kind"], r.get("old"), r.get("new")) for r in out}


# -- the pairing as it was (unchanged behaviour) ---------------------------------------------

def test_matched_identical():
    t = {"id": "t", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "2"]], "n_rows": 2, "n_cols": 2}
    out = tables_diff(_sec([dict(t, id="o")]), _sec([dict(t, id="n")]), None)
    assert len(out) == 1
    assert out[0]["kind"] == "matched"
    assert out[0]["identical"] is True
    assert out[0]["rows_changed"] == 0


def test_matched_changed():
    o = {"id": "o", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "2"]], "n_rows": 2, "n_cols": 2}
    n = {"id": "n", "caption": "Tabelle 1 - Grenzwerte",
         "cells": [["a", "1"], ["b", "3"]], "n_rows": 2, "n_cols": 2}
    out = tables_diff(_sec([o]), _sec([n]), None)
    assert out[0]["kind"] == "matched"
    assert out[0]["identical"] is False
    assert out[0]["rows_changed"] >= 1


def test_new_and_removed():
    # clearly dissimilar captions -> no pairing
    o = {"id": "o", "caption": "Tabelle A - Grenzwerte der Oberschwingungen",
         "cells": [["x", "1"]], "n_rows": 1, "n_cols": 2}
    n = {"id": "n", "caption": "Tabelle Z - Antragsformular Seite 1",
         "cells": [["y", "2"]], "n_rows": 1, "n_cols": 2}
    out = tables_diff(_sec([o]), _sec([n]), None)
    kinds = sorted(t["kind"] for t in out)
    assert kinds == ["new", "removed"]


# -- 1..2: caption hygiene -------------------------------------------------------------------

def test_a_broken_caption_does_not_count():
    """"Tabelle 10 empfohlen." is the end of the preceding sentence: keyword and number,
    but no separator and no descriptive text. It counts as a missing caption -- and it
    stays in the record, because the display still uses it."""
    assert caption_text("Tabelle 10 empfohlen.") is None
    assert caption_text("Tabelle 12 empfohlen.") is None
    assert caption_text("") is None
    assert caption_text(None) is None
    assert caption_text("Tabelle 5 -") is None            # separator, but no text
    assert caption_text("Die Werte nach Tabelle 7 - siehe oben") is None   # no leading keyword

    # a broken caption must not pair by caption: B and Y share the shape of a caption and
    # nothing else, so they stay apart
    out = tables_diff(_sec([TAB_B]), _sec([TAB_Y]), None)
    assert _pairs(out) == {("removed", "o_B", None), ("new", None, "n_Y")}

    # the raw string survives untouched in the report
    assert [r["caption"] for r in out if r["kind"] == "removed"] == ["Tabelle 10 empfohlen."]


def test_a_proper_caption_counts():
    """Keyword, number, separator, text -- and what is returned is the text without the
    number, because the number is the least reliable part of a renumbered caption."""
    assert caption_text("Tabelle 10 - Einstellwerte") == "Einstellwerte"
    assert caption_text("Tabelle 11 – Empfohlene Einstellwerte") == "Empfohlene Einstellwerte"
    assert caption_text("Tabelle A.1: Kennwerte der Anlage") == "Kennwerte der Anlage"
    assert caption_text("Bild 3 - Prinzipschaltbild") == "Prinzipschaltbild"
    assert caption_text("Table 2 - Setting values") == "Setting values"


# -- 3..4: what the score is made of ---------------------------------------------------------

def test_the_number_does_not_drive_the_pairing():
    """The 10.3.4 case: old 11 belongs to new 17, old 10 to new 16. Comparing the full
    caption made 16 the better partner of 11, because the numbers are as similar as the
    descriptive text."""
    out = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_X, TAB_Y]), None)

    assert _pairs(out) == {("matched", "o_A", "n_Y"), ("matched", "o_B", "n_X")}


def test_content_can_outweigh_the_caption():
    """Two candidates, one with the more similar caption, one with the same content. The
    higher of the two signals decides, so the content wins -- it used to be a fallback
    only reached when a caption was missing."""
    old = {"id": "o", "caption": "Tabelle 5 - Grenzwerte der Spannung am Netzanschlusspunkt",
           "cells": [["Punkt", "Wert"], ["U max", "1,10 Un"], ["U min", "0,90 Un"]],
           "n_rows": 3, "n_cols": 2}
    by_caption = {"id": "n_cap",
                  "caption": "Tabelle 5 - Grenzwerte der Frequenz am Netzanschlusspunkt",
                  "cells": [["Stufe", "Zeit"], ["1", "10 s"], ["2", "20 s"]],
                  "n_rows": 3, "n_cols": 2}
    by_content = {"id": "n_con", "caption": "Tabelle 9 - Anforderungen an die Dokumentation",
                  "cells": [["Punkt", "Wert"], ["U max", "1,10 Un"], ["U min", "0,90 Un"]],
                  "n_rows": 3, "n_cols": 2}
    out = tables_diff(_sec([old]), _sec([by_caption, by_content]), None)

    assert ("matched", "o", "n_con") in _pairs(out)
    assert ("new", None, "n_cap") in _pairs(out)


# -- 5..7: the assignment --------------------------------------------------------------------

def test_assignment_is_global_not_greedy():
    """Taking the best free partner for each new table in turn makes the document order
    of the new tables decide: X grabs A before Y is ever asked. A global assignment gives
    the same answer for either order."""
    forward = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_X, TAB_Y]), None)
    reverse = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_Y, TAB_X]), None)

    assert _pairs(forward) == _pairs(reverse)
    assert _pairs(forward) == {("matched", "o_A", "n_Y"), ("matched", "o_B", "n_X")}
    # the old order too -- the assignment is over the whole chapter, not over one side
    assert _pairs(tables_diff(_sec([TAB_B, TAB_A]), _sec([TAB_X, TAB_Y]), None)) \
        == _pairs(forward)


def test_a_pair_below_the_threshold_splits():
    """The threshold applies after the assignment. B and Y are each other's only
    candidate and get assigned, but at 0.198 the pair is not a pair."""
    out = tables_diff(_sec([TAB_B]), _sec([TAB_Y]), None)

    assert _pairs(out) == {("removed", "o_B", None), ("new", None, "n_Y")}
    assert all("confidence" not in r for r in out)


def test_unmatched_tables_are_reported_both_ways():
    """Surplus on the old side is reported as removed, surplus on the new side as new --
    the assignment pairs at most min(old, new) tables."""
    surplus_new = tables_diff(_sec([TAB_A]), _sec([TAB_X, TAB_Y]), None)
    assert _pairs(surplus_new) == {("matched", "o_A", "n_Y"), ("new", None, "n_X")}

    surplus_old = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_Y]), None)
    assert _pairs(surplus_old) == {("matched", "o_A", "n_Y"), ("removed", "o_B", None)}


# -- 8..9: unchanged guarantees --------------------------------------------------------------

def test_fragment_tables_stay_excluded():
    """Captionless one-column remnants of the extraction (equation numbers) stay out of
    the diff; a real captionless table with content stays in."""
    fragment = {"id": "frag", "caption": "", "cells": [["(11)"]], "n_rows": 1, "n_cols": 1}
    real = {"id": "abk", "caption": "", "n_rows": 3, "n_cols": 2,
            "cells": [["EZA", "Erzeugungsanlage"], ["EZE", "Erzeugungseinheit"],
                      ["NAP", "Netzanschlusspunkt"]]}
    out = tables_diff(_sec([fragment, real]), _sec([dict(fragment, id="frag2"),
                                                    dict(real, id="abk2")]), None)

    assert _pairs(out) == {("matched", "abk", "abk2")}


def test_tables_diff_is_deterministic():
    """Byte reproducibility: the same input gives the same records in the same order,
    matched/new in the document order of the new tables, removed after them."""
    first = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_X, TAB_Y]), None)
    second = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_X, TAB_Y]), None)
    assert first == second

    with_removed = tables_diff(_sec([TAB_A, TAB_B]), _sec([TAB_Y]), None)
    assert [r["kind"] for r in with_removed] == ["matched", "removed"]
