"""AP-24, part 2: a paragraph split in two is one change, not two.

The new edition splits an old paragraph; the aligner pairs the first half and reports the
second one as ``new`` although it stands verbatim in the old text -- the same sentence
counted once as a deletion and once as an addition (78 of 1269 ``new`` records at 4110,
75 of 1048 at 4120). A post-pass *after* all existing passes extends the existing pairing
to a 1:2 pairing and reports it as ``split``.

Synthetic texts; the similarity backend is a fake keyed on the first token, so the tests
need neither a corpus nor a real vectorizer.
"""
from __future__ import annotations

import numpy as np

from normpare.stages.align.paras import align_chapter

TAU = 0.62

SATZ_A = "Alpha Der Betreiber prueft die Einstellwerte vor der ersten Inbetriebnahme."
SATZ_B = "Beta Die Vorgabe erfolgt fuer jede Erzeugungseinheit gesondert und dauerhaft."
SATZ_C = "Gamma Der Nachweis wird mit dem Pruefbericht der Messstelle gefuehrt."
SATZ_D = "Delta Die Meldung enthaelt den Zeitpunkt und die Dauer der Abschaltung."


class TagBackend:
    """Similarity by tag: same first token -> 0.9, otherwise 0.1.

    Keeps the fixtures readable -- which paragraphs pair is stated in the text, not in a
    hand-written matrix.
    """

    def sim_matrix(self, a, b):
        return np.array([[0.9 if x.split()[0] == y.split()[0] else 0.1 for y in b] for x in a],
                        dtype=float)


def _paras(*texts) -> list[dict]:
    return [{"id": f"p{i}", "kind": "text", "n1": t} for i, t in enumerate(texts)]


def test_a_split_paragraph_becomes_split():
    old = _paras(SATZ_A + " " + SATZ_B)
    new = _paras(SATZ_A, SATZ_B)
    links = align_chapter(old, new, TagBackend(), TAU)
    assert [l["kind"] for l in links] == ["split"]
    assert links[0]["o"] == [0] and links[0]["n"] == [0, 1]


def test_only_one_extra_paragraph_is_absorbed():
    old = _paras(SATZ_A + " " + SATZ_B + " " + SATZ_C)
    new = _paras(SATZ_A, SATZ_B, SATZ_C)
    stats: dict = {}
    links = align_chapter(old, new, TagBackend(), TAU, stats=stats)
    split = [l for l in links if l["kind"] == "split"]
    assert len(split) == 1 and split[0]["n"] == [0, 1]
    assert [l["n"] for l in links if l["kind"] == "new"] == [[2]]
    assert stats["split_absorbed"] == 1
    assert stats["split_skipped"] == 1


def test_a_short_fragment_is_not_absorbed():
    fragment = "Beta kurz und knapp."          # under 40 compare_key characters
    old = _paras(SATZ_A + " " + fragment)
    new = _paras(SATZ_A, fragment)
    links = align_chapter(old, new, TagBackend(), TAU)
    assert [l["kind"] for l in links] == ["similar", "new"]


def test_absorption_is_deterministic():
    # Two already paired old paragraphs both contain the fragment: the shared part is the
    # whole fragment in either case, so the smaller paragraph id decides.
    old = _paras(SATZ_A + " " + SATZ_B, SATZ_C + " " + SATZ_B)
    new = _paras(SATZ_A, SATZ_C, SATZ_B)
    links = align_chapter(old, new, TagBackend(), TAU)
    by_old = {l["o"][0]: l for l in links if l["o"]}
    assert by_old[0]["kind"] == "split" and by_old[0]["n"] == [0, 2]
    assert by_old[1]["kind"] == "similar" and by_old[1]["n"] == [1]
    again = align_chapter(old, new, TagBackend(), TAU)
    assert again == links


def test_existing_passes_are_untouched():
    # No candidate: the added paragraph stands nowhere in the old text.
    old = _paras(SATZ_A)
    new = _paras(SATZ_A + " zusaetzlich geprueft", SATZ_D)
    stats: dict = {}
    links = align_chapter(old, new, TagBackend(), TAU, stats=stats)
    assert links == [{"o": [0], "n": [0], "kind": "similar", "conf": 0.9},
                     {"o": [], "n": [1], "kind": "new", "conf": 0.0}]
    assert stats == {}
