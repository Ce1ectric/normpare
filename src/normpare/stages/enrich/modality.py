"""
modality.py -- normative modality PER SENTENCE (not one label per paragraph).

Scale (normative bindingness): darf_nicht > muss > sollte > darf > kann > informativ.
Also captures "ist/sind ... zu <verb>" as a muss equivalent (colleague finding)
and "ANMERKUNG"/"BEISPIEL" markers as informativ.
"""
from __future__ import annotations
import re
from ...text.textnorm import split_sentences

RANK = {"darf_nicht": 5, "muss": 4, "sollte": 3, "darf": 2, "kann": 1, "informativ": 0}

_P_DARF_NICHT = re.compile(r"\b(darf|dürfen)\b[^.;]{0,60}?\bnicht\b|\bunzulässig\b|\bnicht zulässig\b", re.I)
_P_MUSS   = re.compile(r"\b(muss|müssen|hat zu|haben zu)\b|\b(ist|sind)\b[^.;]{0,80}?\bzu\s+\w+en\b|\berforderlich\b", re.I)
_P_SOLLTE = re.compile(r"\b(sollte|sollten|soll|sollen)\b", re.I)
_P_DARF   = re.compile(r"\b(darf|dürfen|zulässig)\b", re.I)
_P_KANN   = re.compile(r"\b(kann|können)\b", re.I)
_P_INFO   = re.compile(r"^\s*(ANMERKUNG|BEISPIEL|Anmerkung \d|Beispiel \d)", re.U)


def classify_sentence(s: str) -> str:
    if _P_INFO.match(s):
        return "informativ"
    if _P_DARF_NICHT.search(s):
        return "darf_nicht"
    if _P_MUSS.search(s):
        return "muss"
    if _P_SOLLTE.search(s):
        return "sollte"
    if _P_DARF.search(s):
        return "darf"
    if _P_KANN.search(s):
        return "kann"
    return "informativ"


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
