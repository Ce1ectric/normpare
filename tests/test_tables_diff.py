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

AP-23 adds two things on top of that:

* the head of a table carries its identity, the body does not -- a captionless form whose
  first rows are the shared header pairs even when everything below differs,
* a table that changed chapters is a move, not a deletion plus an addition: after the
  chapter-wise pairing a document-wide pass puts the leftovers of every chapter against
  each other and reports a hit as ``moved_away`` at the old place and ``moved_in`` at the
  new one, at a higher threshold, because a move across chapters is the stronger claim.

The material is synthetic and shaped like 10.3.4, no norm text. No network.
"""
import json

from normpare.stages.diff import caption_text, cross_chapter_tables, tables_diff

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


# -- material for the form tables (AP-23, part 1) --------------------------------------------
# A captionless form: the first rows are the header both editions share, everything below
# is filled in and differs. Measured with the production measure: over three rows 1.000,
# over ten rows 0.340 -- the same collapse as the real forms (4110 B.11.2 0.850 -> 0.407).
FORM_HEAD = [["Erdungsprotokoll der Erzeugungsanlage", "", ""],
             ["Anlagenbezeichnung", "Standort", "Netzbetreiber"],
             ["Pruefer", "Pruefdatum", "Unterschrift"]]
FORM_OLD = {"id": "o_form", "caption": "", "n_rows": 10, "n_cols": 3,
            "cells": FORM_HEAD + [["Erdungswiderstand", "0,84 Ohm", "gemessen 12.03.2019"],
                                  ["Schleifenimpedanz", "0,21 Ohm", "gemessen 12.03.2019"],
                                  ["Erder Bauart", "Ringerder Kupfer 50 mm2", "Tiefe 0,8 m"],
                                  ["Messgeraet", "Typ MZC-310S", "kalibriert 02.2019"],
                                  ["Witterung", "trocken", "9 Grad Celsius"],
                                  ["Bemerkung", "keine Beanstandung", "Blatt 1 von 1"],
                                  ["Anlage", "Beiblatt Messprotokoll", "Anlage 2"]]}
FORM_NEW = {"id": "n_form", "caption": "", "n_rows": 10, "n_cols": 3,
            "cells": FORM_HEAD + [["Ausbreitungswiderstand", "1,37 Ohm", "ermittelt 04.11.2025"],
                                  ["Erdungsspannung", "63 V", "ermittelt 04.11.2025"],
                                  ["Erder Bauart", "Fundamenterder Stahl verzinkt", "Tiefe 1,2 m"],
                                  ["Messverfahren", "Strom-Spannungs-Verfahren", "Zangenmessung"],
                                  ["Beurteilung", "Grenzwert eingehalten", "Klasse 2"],
                                  ["Naechste Pruefung", "Wiederholung 2029", "Blatt 1 von 2"],
                                  ["Beiblatt", "Lageplan der Erder", "Anhang 4"]]}
#: A different form, same shape, other header. Measured against FORM_OLD: 0.199.
OTHER_FORM = {"id": "n_other", "caption": "", "n_rows": 10, "n_cols": 3,
              "cells": [["Komponentenzertifikat", "", ""],
                        ["Hersteller", "Typ", "Seriennummer"],
                        ["Zertifizierungsstelle", "Nummer", "Gueltig bis"],
                        ["Wechselrichter", "SG-4000", "88-2211"],
                        ["Transformator", "TR-630", "88-2212"],
                        ["Schutzeinrichtung", "SE-77", "88-2213"],
                        ["Regelung", "RG-12", "88-2214"],
                        ["Messwandler", "MW-5", "88-2215"],
                        ["Steuerung", "ST-9", "88-2216"],
                        ["Kommunikation", "KM-3", "88-2217"]]}

# -- material for the move across chapters (AP-23, part 2) -----------------------------------
# Shaped like 4110 "Anlagenzertifikat B": the same table, in the old edition in 11.4.21 and
# in the new one in 11.4.24 (measured there: 1.000).
CERT_CELLS = [["Anlagenzertifikat B", "", ""],
              ["Pruefgegenstand", "Nachweis", "Beiblatt"],
              ["Modellvalidierung", "Simulationsbericht", "Anlage 1"],
              ["Konformitaetsnachweis", "Pruefbericht", "Anlage 2"]]
CERT_OLD = {"id": "o_cert", "caption": "", "n_rows": 4, "n_cols": 3, "cells": CERT_CELLS}
CERT_NEW = dict(CERT_OLD, id="n_cert")
#: The same table with one row rewritten below the header -- still the same table (1.000
#: over the header), but with a changed row.
CERT_NEW_CHANGED = dict(CERT_OLD, id="n_cert_chg",
                        cells=CERT_CELLS[:3] + [["Konformitaetsnachweis", "Messbericht",
                                                 "Anlage 5"]])
#: A weaker competitor: the header itself differs in one cell (measured 0.939).
CERT_NEW_WEAKER = dict(CERT_OLD, id="n_cert_wk",
                       cells=[CERT_CELLS[0], ["Pruefgegenstand", "Nachweis", "Anhang"]]
                       + CERT_CELLS[2:])

# A candidate pair between the two thresholds: 0.667 -- the chapter-internal 0.55 would
# take it, the cross-chapter 0.80 does not.
WEAK_OLD = {"id": "o_weak", "caption": "Tabelle 23 - Bewertungsumfang fuer ein "
                                       "Anlagenzertifikat B",
            "cells": [["Nachweis", "Umfang"], ["Modellvalidierung", "vollstaendig"],
                      ["Messbericht", "je Einheit"]], "n_rows": 3, "n_cols": 2}
WEAK_NEW = {"id": "n_weak", "caption": "Tabelle 41 - Pruefumfang eines Zertifikats fuer "
                                       "Speicher",
            "cells": [["Nachweis", "Umfang"], ["Modellrechnung", "in Teilen"],
                      ["Pruefbericht", "je Anlage"]], "n_rows": 3, "n_cols": 2}


def _sec(tables):
    return [{"tables": tables}]


def _pairs(out):
    """The result as a comparable set, independent of the record order."""
    return {(r["kind"], r.get("old"), r.get("new")) for r in out}


def _document(*spec):
    """A whole document: every chapter paired chapter-wise, as ``build_synopse`` does it.

    Each entry of ``spec`` is ``(mapping_id, [old tables], [new tables])``; returned are
    the chapter records and the two id -> table maps the document-wide pass needs.
    """
    chapters, om, nm = [], {}, {}
    for mid, olds, news in spec:
        chapters.append({"mapping_id": mid,
                         "tables_diff": tables_diff(_sec(olds), _sec(news), None)})
        om.update({t["id"]: t for t in olds})
        nm.update({t["id"]: t for t in news})
    return chapters, om, nm


def _by_chapter(chapters):
    return {ch["mapping_id"]: ch["tables_diff"] for ch in chapters}


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

    # AP-23: the document-wide pass is deterministic too -- same document, same records
    # in the same order, down to the order of the keys
    def once():
        chapters, om, nm = _document(("11.4.21", [CERT_OLD], []),
                                     ("11.4.24", [], [CERT_NEW_CHANGED]),
                                     ("11.6.6", [], [CERT_NEW]))
        moves = cross_chapter_tables(chapters, om, nm)
        return json.dumps([chapters, moves], ensure_ascii=False)

    assert once() == once()


# -- AP-23, part 1: the head carries the identity --------------------------------------------

def test_a_form_table_pairs_on_its_header():
    """A captionless form (an earthing protocol) whose first rows are the shared header and
    whose body is filled in differently is still the same form. Over the first three rows
    the two are identical (1.000); reaching further down the content measures the entries
    instead of the identity and the pair falls apart (0.340 over ten rows)."""
    out = tables_diff(_sec([FORM_OLD]), _sec([FORM_NEW]), None)

    assert _pairs(out) == {("matched", "o_form", "n_form")}
    assert out[0]["rows_changed"] >= 1        # the body did change, and it is reported


def test_two_different_forms_do_not_pair():
    """The head decides in both directions: two forms with different headers stay apart,
    however similar their shape is (0.199)."""
    out = tables_diff(_sec([FORM_OLD]), _sec([OTHER_FORM]), None)

    assert _pairs(out) == {("removed", "o_form", None), ("new", None, "n_other")}


# -- AP-23, part 2: the move across chapter boundaries ---------------------------------------

def test_a_table_moved_to_another_chapter_is_found():
    """The certificate table stands in 11.4.21 in the old edition and in 11.4.24 in the new
    one. Chapter-wise it is a deletion in one chapter and an addition in the other; the
    document-wide pass makes one move out of the two."""
    chapters, om, nm = _document(("11.4.21", [CERT_OLD], []), ("11.4.24", [], [CERT_NEW]))
    moves = cross_chapter_tables(chapters, om, nm)

    assert [(m["old"], m["new"]) for m in moves] == [("o_cert", "n_cert")]
    rec = _by_chapter(chapters)
    assert [r["kind"] for r in rec["11.4.21"]] == ["moved_away"]
    assert [r["kind"] for r in rec["11.4.24"]] == ["moved_in"]


def test_the_move_carries_both_ends():
    """Both ends name the other one: the record at the old place carries the id of the new
    table and the chapter it went to, the record at the new place the id of the old table
    and the chapter it came from."""
    chapters, om, nm = _document(("11.4.21", [CERT_OLD], []), ("11.4.24", [], [CERT_NEW]))
    cross_chapter_tables(chapters, om, nm)
    away = _by_chapter(chapters)["11.4.21"][0]
    into = _by_chapter(chapters)["11.4.24"][0]

    assert away["old"] == "o_cert" and away["new"] == "n_cert"
    assert away["moved_to_chapter"] == "11.4.24"
    assert into["old"] == "o_cert" and into["new"] == "n_cert"
    assert into["moved_from_chapter"] == "11.4.21"
    assert away["confidence"] == into["confidence"] == 1.0


def test_a_weak_cross_chapter_match_stays_removed():
    """A move across chapters is the stronger claim and needs the stronger evidence. This
    pair scores 0.667: inside a chapter it would be a pair, across chapters it is not."""
    chapters, om, nm = _document(("11.4", [WEAK_OLD], []), ("12.2", [], [WEAK_NEW]))
    moves = cross_chapter_tables(chapters, om, nm)

    assert moves == []
    rec = _by_chapter(chapters)
    assert [r["kind"] for r in rec["11.4"]] == ["removed"]
    assert [r["kind"] for r in rec["12.2"]] == ["new"]
    # the pair would be taken inside a chapter -- it is the threshold, not the measure
    assert _pairs(tables_diff(_sec([WEAK_OLD]), _sec([WEAK_NEW]), None)) \
        == {("matched", "o_weak", "n_weak")}


def test_the_cross_chapter_pass_runs_after_the_chapter_pass():
    """Only what the chapter-wise pairing left over is a candidate. The old table is already
    paired inside its own chapter, so the identical table in another chapter finds nothing
    and stays an addition -- one table cannot be the partner of two."""
    chapters, om, nm = _document(("10.1", [CERT_OLD], [CERT_NEW_CHANGED]),
                                 ("12.3", [], [CERT_NEW]))
    moves = cross_chapter_tables(chapters, om, nm)

    assert moves == []
    rec = _by_chapter(chapters)
    assert [r["kind"] for r in rec["10.1"]] == ["matched"]
    assert [r["kind"] for r in rec["12.3"]] == ["new"]


def test_the_cross_chapter_pass_is_global():
    """One leftover old table, two candidates in two other chapters. The assignment is over
    the whole document, so the better one wins no matter in which order the chapters stand
    -- a pass walking the chapters in document order would take the weaker candidate first."""
    forward, om, nm = _document(("11.4.21", [CERT_OLD], []),
                                ("11.5", [], [CERT_NEW_WEAKER]),
                                ("11.4.24", [], [CERT_NEW]))
    moves_f = cross_chapter_tables(forward, om, nm)
    reverse, om_r, nm_r = _document(("11.4.24", [], [CERT_NEW]),
                                    ("11.5", [], [CERT_NEW_WEAKER]),
                                    ("11.4.21", [CERT_OLD], []))
    moves_r = cross_chapter_tables(reverse, om_r, nm_r)

    assert [(m["old"], m["new"]) for m in moves_f] == [("o_cert", "n_cert")]
    assert [(m["old"], m["new"]) for m in moves_r] == [("o_cert", "n_cert")]
    assert [r["kind"] for r in _by_chapter(forward)["11.5"]] == ["new"]
    assert [r["kind"] for r in _by_chapter(reverse)["11.5"]] == ["new"]


def test_moved_tables_report_changed_rows():
    """A moved table is compared row by row like a paired one -- moving is not a reason to
    stop looking at what changed on the way."""
    chapters, om, nm = _document(("11.4.21", [CERT_OLD], []),
                                 ("11.4.24", [], [CERT_NEW_CHANGED]))
    cross_chapter_tables(chapters, om, nm)
    away, into = _by_chapter(chapters)["11.4.21"][0], _by_chapter(chapters)["11.4.24"][0]

    assert away["rows_changed"] == into["rows_changed"] == 1
    assert away["identical"] is False and into["identical"] is False

    same, om2, nm2 = _document(("11.4.21", [CERT_OLD], []), ("11.4.24", [], [CERT_NEW]))
    cross_chapter_tables(same, om2, nm2)
    assert _by_chapter(same)["11.4.21"][0]["rows_changed"] == 0
    assert _by_chapter(same)["11.4.21"][0]["identical"] is True


def test_existing_kinds_keep_their_meaning():
    """The two new values are added, none of the three old ones changes: a chapter-internal
    pair stays ``matched``, and a deletion or addition without a partner anywhere in the
    document stays ``removed`` or ``new``, untouched down to the last key."""
    chapters, om, nm = _document(("10.3.4", [TAB_A, TAB_B], [TAB_X, TAB_Y]),
                                 ("11.1", [dict(TAB_B, id="o_B2")], []),
                                 ("12.1", [], [dict(TAB_Y, id="n_Y2")]))
    before = json.loads(json.dumps(chapters))
    moves = cross_chapter_tables(chapters, om, nm)

    assert moves == []
    assert chapters == before
    rec = _by_chapter(chapters)
    assert {r["kind"] for r in rec["10.3.4"]} == {"matched"}
    assert [r["kind"] for r in rec["11.1"]] == ["removed"]
    assert [r["kind"] for r in rec["12.1"]] == ["new"]
