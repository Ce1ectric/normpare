"""
modality.py -- normative modality PER SENTENCE (not one label per paragraph).

Scale (normative bindingness): darf_nicht > muss > sollte > darf > kann > informativ.

Two things beyond the modal verbs:

* **The modal infinitive**, in both of its German forms. Analytic, with ``zu`` as a word
  of its own ("ist ... zu prüfen"), and synthetic, with ``zu`` as an infix inside the verb
  ("ist nachzuweisen", "sind einzuhalten", "hat sicherzustellen"). Both say the same as
  "muss ... werden" and carry the same rank. Until AP-05 only the analytic form was
  covered although the docstring claimed otherwise; the synthetic one accounts for about
  10 % of the sentences of a VDE-AR-N (measured on 4110: 1113 sentences,
  ``runs/AP-05_2026-08-10/BERICHT.md``).
* **"ANMERKUNG"/"BEISPIEL"** markers as informativ.

What the modal infinitive is *not* is the **permission frame** ``ist/sind (es)
<predicate>`` in front of the same infinitive ("ist es zulässig, ... heranzuziehen").
There the infinitive is the complement of a predicative, and the sentence permits instead
of obliging. AP-05 read one such sentence in thirty as a duty (AP-05a).

Deontic type and polarity are kept apart from the label, because negation does two
different things: a negated permission is a **prohibition** ("darf nicht"), a negated
necessity lets a duty **fall away** ("muss nicht", "braucht nicht", "nicht erforderlich").
Both used to end up on the binding side of the scale, the second one wrongly. The six
labels are unchanged -- they are what ``norm_doc.json`` stores and what the report stages
filter on; the finer reading is available through :func:`deontic`.
"""
from __future__ import annotations
import re
from ...text.textnorm import split_sentences

RANK = {"darf_nicht": 5, "muss": 4, "sollte": 3, "darf": 2, "kann": 1, "informativ": 0}

#: Deontic type and polarity to the vocabulary of the corpus specification
#: (``tests/fixtures/synthetic/erwartung.toml``). A duty that falls away and a permission
#: meet in ``zulaessigkeit``: both leave the addressee free. Which of the two it was stays
#: readable in ``type``/``polarity``.
DEONTIC_CLASS = {
    ("pflicht", "positiv"): "anforderung",
    ("pflicht", "negativ"): "zulaessigkeit",
    ("erlaubnis", "positiv"): "zulaessigkeit",
    ("erlaubnis", "negativ"): "verbot",
    ("empfehlung", "positiv"): "empfehlung",
    ("moeglichkeit", "positiv"): "moeglichkeit",
    ("keine", "positiv"): "informativ",
}

# -- the synthetic modal infinitive --------------------------------------------------------

#: Separable prefixes that can carry the ``zu`` infix -- a closed class. This is what keeps
#: the pattern off nouns: ``Bezugsanlagen``, ``Voraussetzungen``, ``Kurzunterbrechungen``
#: all carry the letters ``zu`` at a syllable boundary and end in ``-en``, and a pattern of
#: the shape ``\w+zu\w+en`` reads all of them as duties (164 such hits on 4110).
_PREFIX = (
    "ab|acht|an|auf|aufrecht|aus|bei|bereit|dar|durch|ein|einher|empor|entgegen|entlang|"
    "fern|fest|fort|frei|gegen|gegenüber|gleich|gut|heim|her|herab|heran|herauf|heraus|"
    "herbei|herein|herunter|hervor|hin|hinab|hinauf|hinaus|hinein|hinterher|hinzu|hoch|"
    "instand|klar|kurz|los|mit|nach|nahe|nieder|offen|preis|sicher|stand|statt|still|"
    "teil|tief|über|um|unter|voll|vor|voran|voraus|vorbei|wahr|weg|weiter|wett|wieder|"
    "zu|zurecht|zurück|zusammen"
)

#: Verbs whose stem itself ends in ``-end``. Only for them is a form ending in ``-enden``
#: an infinitive ("anzuwenden"); for every other verb it is the attributive gerundive
#: ("die anzuschließenden Anlagen", "die einzuhaltenden Grenzwerte"), which describes a
#: thing instead of obliging anybody.
_STEM_IN_END = "wenden|senden|enden|blenden|spenden|schänden"

#: prefix(es) + ``zu`` + stem + infinitive ending. Lower case only: a capital first letter
#: makes it a noun ("Auszubildenden"), and a synthetic infinitive practically never opens a
#: sentence in a standard.
_INFINITIVE = (rf"\b(?:{_PREFIX}){{1,2}}"
               rf"zu(?:{_STEM_IN_END}|(?!\w*enden\b)[a-zäöüß]{{2,}}?e[lr]?n)\b")

#: Words that govern an infinitive of their own: an aim ("um ... einzuhalten"), its
#: absence ("ohne ... einzuhalten"), a substitution ("statt ... einzuhalten"). None of them
#: obliges anyone, so they cut the link between an auxiliary and the infinitive.
_OTHER_GOVERNOR = r"\bum\b|\bohne\b|\b(?:an)?statt\b"

#: What may stand between the auxiliary and the infinitive: the sentence is the outer bound
#: (the text is already split into sentences, so a ``.`` inside one is an abbreviation), a
#: semicolon separates clauses, and no other governor may intervene. Deliberately without a
#: length bound -- measured on 4110, the auxiliary of a duty stands up to 400 characters
#: before its infinitive, behind relative clauses and parentheses.
_GAP = rf"(?:(?!{_OTHER_GOVERNOR})[^;])*?"

#: A verb-final clause puts the auxiliary behind the infinitive ("die nachzuweisen sind"),
#: possibly after a coordinated second infinitive -- but never across a comma, which would
#: reach into the next clause.
_TAIL = rf"(?:(?!{_OTHER_GOVERNOR})[^,;])*?"

_AUX = "ist|sind|war|waren|sei|seien|wäre|wären|hat|haben|hatte|hatten|hätte|hätten"

_P_INFINITIV_SYNTH = re.compile(rf"\b(?i:{_AUX})\b{_GAP}{_INFINITIVE}"
                                rf"|{_INFINITIVE}{_TAIL}\s(?i:{_AUX})\b")

# -- the permission frame ------------------------------------------------------------------

#: Predicates that turn ``ist/sind (es) ... , ... zu <verb>`` into a permission instead of a
#: duty ("ist es zulässig, kürzere Mittelungszeiträume heranzuziehen"). A closed list on
#: purpose: the frame is not recognisable by structure. "ist **sorgfältig** durchzuführen"
#: has an adjective in the same slot and obliges; the difference is the predicate itself.
#: Uninflected only -- predicatively German leaves the adjective bare ("ist zulässig"),
#: attributively it inflects ("die zulässige Abweichung"), and only the first one governs
#: an infinitive of its own.
_PERMISSION_PREDICATE = ("zulässig|möglich|erlaubt|gestattet|statthaft|freigestellt|"
                         "optional|nicht erforderlich|nicht notwendig|nicht zwingend")

#: ``ist``/``sind``, an optional ``es``, up to two adverbs ("ist es **zudem** zulässig"),
#: then the predicate. No comma may fall inside the frame -- it is one clause, not a
#: sentence-wide property.
_P_PERMISSION_FRAME = re.compile(
    rf"\b(?:ist|sind)\b(?:\s+es\b)?(?:\s+(?!(?:{_PERMISSION_PREDICATE})\b)[a-zäöüß]+\b){{0,2}}"
    rf"\s+(?P<pred>{_PERMISSION_PREDICATE})\b", re.I)

#: "Es steht dem Betreiber frei, ... anzuwenden" -- same job, fixed wording instead of a
#: predicate list.
_P_FREE_CHOICE = re.compile(r"\bsteht\b(?:\s+[\wäöüß]+\b){0,3}?\s+(?P<pred>frei)\b", re.I)


def _mask_permission_frames(s: str) -> str:
    """Cut the auxiliary out of every permission frame, keep the predicate.

    ``;`` is what the pattern above already treats as a hard boundary, so replacing the
    auxiliary with it takes the frame out of reach for both forms of the modal infinitive
    at once -- the analytic one ("ist es zulässig, ... zu erfassen") and the synthetic one
    ("ist es zulässig, ... heranzuziehen"). It also cuts the link when the offending
    auxiliary comes from another clause altogether ("... erfüllt **sind**, steht es dem
    Betreiber frei, ... anzuwenden").

    The predicate stays so that the sentence keeps being read for what it is: ``zulässig``
    still reaches :data:`_P_DARF`, ``nicht erforderlich`` still reaches
    :data:`_P_MUSS_NICHT`.
    """
    for pattern in (_P_PERMISSION_FRAME, _P_FREE_CHOICE):
        s = pattern.sub(lambda m: " ; " + m.group("pred"), s)
    return s

# -- modal verbs -----------------------------------------------------------------------------

_P_DARF_NICHT = re.compile(r"\b(darf|dürfen)\b[^.;]{0,60}?\bnicht\b|\bunzulässig\b|\bnicht zulässig\b", re.I)
_P_MUSS   = re.compile(r"\b(muss|müssen|hat zu|haben zu)\b|\b(ist|sind)\b[^.;]{0,80}?\bzu\s+\w+en\b|\berforderlich\b", re.I)
_P_SOLLTE = re.compile(r"\b(sollte|sollten|soll|sollen)\b", re.I)
_P_DARF   = re.compile(r"\b(darf|dürfen|zulässig)\b", re.I)
_P_KANN   = re.compile(r"\b(kann|können)\b", re.I)
_P_INFO   = re.compile(r"^\s*(ANMERKUNG|BEISPIEL|Anmerkung \d|Beispiel \d)", re.U)

#: Negated necessity: the duty falls away. "nicht erforderlich" belongs here as well -- it
#: is the same construction and was read as ``muss`` in all 22 occurrences of 4110.
_P_MUSS_NICHT = re.compile(r"\b(muss|müssen|braucht|brauchen)\b[^.;]{0,60}?\bnicht\b"
                           r"|\bnicht\b[^.;]{0,30}?\berforderlich\b", re.I)


def _is_duty(s: str) -> bool:
    """A duty stated positively -- modal verb, analytic or synthetic modal infinitive.

    On the masked sentence: a permission frame governs the infinitive itself and leaves
    nobody obliged.
    """
    s = _mask_permission_frames(s)
    return bool(_P_MUSS.search(s) or _P_INFINITIV_SYNTH.search(s))


def deontic(s: str) -> dict:
    """Modality of one sentence as label, deontic type, polarity and class.

    ``label`` is one of :data:`RANK` and is what the pipeline stores. ``type`` and
    ``polarity`` say what the label alone cannot: "darf nicht" and "muss nicht" are both
    negated, but the first forbids (negated permission) while the second releases (negated
    necessity).
    """
    type_, polarity = _classify(s)
    return {"label": _LABEL[(type_, polarity)], "type": type_, "polarity": polarity,
            "deontic_class": DEONTIC_CLASS[(type_, polarity)]}


def _classify(s: str) -> tuple[str, str]:
    if _P_INFO.match(s):
        return "keine", "positiv"
    if _P_DARF_NICHT.search(s):
        return "erlaubnis", "negativ"
    # The waiver is read on the masked sentence, so that the infinitive of the waiver's own
    # frame cannot re-instate the duty it just dropped ("Es ist nicht erforderlich, die
    # Unterlagen vorzulegen.").
    masked = _mask_permission_frames(s)
    waived = _P_MUSS_NICHT.search(masked)
    # A sentence may waive one duty and state another ("Ein Nachweis ist nicht
    # erforderlich, die Werte sind jedoch aufzubewahren."). The waiver only decides when
    # nothing outside its own span obliges -- losing a duty is the worse error.
    if waived and not _is_duty(masked[:waived.start()] + " " + masked[waived.end():]):
        return "pflicht", "negativ"
    if _is_duty(s):
        return "pflicht", "positiv"
    if _P_SOLLTE.search(s):
        return "empfehlung", "positiv"
    if _P_DARF.search(s):
        return "erlaubnis", "positiv"
    if _P_KANN.search(s):
        return "moeglichkeit", "positiv"
    return "keine", "positiv"


#: Deontic type and polarity to the six-value scale. A duty that falls away is filed under
#: ``darf``: per ISO/IEC Directives Part 2 "braucht nicht" belongs to the vocabulary of
#: permission, and rank 2 makes ``shift()`` read a dropped duty as a relaxation instead of
#: as an unchanged obligation.
_LABEL = {
    ("pflicht", "positiv"): "muss",
    ("pflicht", "negativ"): "darf",
    ("erlaubnis", "positiv"): "darf",
    ("erlaubnis", "negativ"): "darf_nicht",
    ("empfehlung", "positiv"): "sollte",
    ("moeglichkeit", "positiv"): "kann",
    ("keine", "positiv"): "informativ",
}


def classify_sentence(s: str) -> str:
    return _LABEL[_classify(s)]


def classify_paragraph(text: str) -> dict:
    """Per-sentence classification. Returns:
    {"max": strongest modality, "counts": {...}, "sentences": [(sentence, modality), ...]}"""
    sents = split_sentences(text)
    labeled = [(s, classify_sentence(s)) for s in sents]
    counts: dict[str, int] = {}
    for _, m in labeled:
        counts[m] = counts.get(m, 0) + 1
    mx = max((m for _, m in labeled), key=lambda m: RANK[m], default="informativ")
    return {"max": mx, "counts": counts, "sentences": labeled}


def shift(old_max: str, new_max: str) -> str:
    ro, rn = RANK.get(old_max, 0), RANK.get(new_max, 0)
    if rn > ro:
        return "verschaerft"
    if rn < ro:
        return "gelockert"
    return "unveraendert"
