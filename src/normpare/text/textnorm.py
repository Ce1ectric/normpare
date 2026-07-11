"""
textnorm.py -- normalization layers N0-N3 and a sentence splitter.

N0: raw text as extracted.
N1: Unicode NFC, ligatures resolved, de-hyphenation, protected/narrow spaces ->
    regular space, line breaks -> space.  (basis for the syntactic diff)
N2: N1 + whitespace collapsed, typographic quotes/dashes unified.
    (basis for anchor matching)
N3: N2 + lowercase, numbers canonicalized (decimal point, no thousands separators),
    punctuation tolerance.  (basis for the semantic comparison: equal on N3 => cosmetic)
"""
from __future__ import annotations
import re
import unicodedata

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}
_SPACES = "     ⁠﻿"          # nbsp, figure, thin, hair, narrow-nbsp, wj, bom
_QUOTES = {"„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "«": '"', "»": '"'}
_DASHES = {"–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-"}

_SOFT_HYPHEN = "­"
# Real hyphen variants (semantically identical to ASCII '-'): U+2010 hyphen and
# U+2011 non-breaking hyphen. Normalized to '-' already in N1 so that the syntactic
# diff (based on N1) does not create spurious reference/parameter changes for
# non-breaking hyphens in strings like 'EN 60909-0', 'Typ-2', 'IEC 60364-4'.
# En/em dash/minus stay reserved for N2 (they are punctuation, not hyphens).
_NB_HYPHENS = ("‐", "‑")
# De-hyphenation at line end: "Wort- fortsetzung" (lowercase next letter).
# EXCEPTION supplementary hyphen: "Erzeugungs- und Speichereinheit" is preserved.
_HYPH_BREAK = re.compile(r"([a-zäöüß])-\s+(?!(?:und|oder|bzw|sowie|als)\b)([a-zäöüß])")
_WS = re.compile(r"\s+")


def _sanitize_glyphs(t: str) -> str:
    """Defuse broken PDF glyphs: mathematical alphanumerics are normalized to
    ASCII; runs of foreign glyphs (broken ToUnicode maps, e.g. Tamil subscript
    remnants) collapse to a single ellipsis."""
    out, bad_run = [], False
    for c in t:
        o = ord(c)
        if 0x1D400 <= o <= 0x1D7FF:                       # math alphanumeric
            out.append(unicodedata.normalize("NFKC", c))
            bad_run = False
        elif not (o < 0x0250 or 0x0370 <= o <= 0x03FF or 0x1E00 <= o <= 0x1EFF
                  or 0x2000 <= o <= 0x27BF):
            if not bad_run:
                out.append("…")
                bad_run = True
        else:
            out.append(c)
            bad_run = False
    return "".join(out)


def n1(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    # Resolve ligatures FIRST, otherwise _sanitize_glyphs would wrongly turn the
    # presentation-form characters (U+FB00 ff., outside the allowed ranges) into an ellipsis.
    for k, v in _LIGATURES.items():
        t = t.replace(k, v)
    t = _sanitize_glyphs(t)
    t = t.replace(_SOFT_HYPHEN, "")
    for c in _NB_HYPHENS:
        t = t.replace(c, "-")
    for c in _SPACES:
        t = t.replace(c, " ")
    t = t.replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
    # Undo hyphenation at line end (only lowercase -> lowercase, so that real
    # hyphenated compounds like "50-Hz-Netz" stay untouched)
    t = _HYPH_BREAK.sub(r"\1\2", t)
    return t


def n2(text: str) -> str:
    t = n1(text)
    for k, v in _QUOTES.items():
        t = t.replace(k, v)
    for k, v in _DASHES.items():
        t = t.replace(k, v)
    return _WS.sub(" ", t).strip()


_NUM_DE = re.compile(r"(?<![\w,.])(\d{1,3}(?:\.\d{3})+|\d+),(\d+)(?![\w])")   # 1.234,56
_NUM_TAUSEND = re.compile(r"(?<![\w,.])(\d{1,3})\.(\d{3})(?!\d)")

def canon_numbers(t: str) -> str:
    """German number formats -> canonical: drop thousands dot, decimal comma -> dot."""
    def _dec(m):
        ganz = m.group(1).replace(".", "")
        return f"{ganz}.{m.group(2)}"
    t = _NUM_DE.sub(_dec, t)
    t = _NUM_TAUSEND.sub(r"\1\2", t)
    return t


_PUNCT_N3 = re.compile(r"[.,;:!?()\[\]{}\"']")
_INTRAWORD_HYPHEN = re.compile(r"(?<=\w)-(?=\w)")

def n3(text: str) -> str:
    t = n2(text).lower()
    t = canon_numbers(t)
    t = _INTRAWORD_HYPHEN.sub("", t)     # de-hyphenation / compound hyphens
    t = _PUNCT_N3.sub(" ", t)
    return _WS.sub(" ", t).strip()


_KEEP_CK = re.compile(r"[0-9a-zäöüß%<>≤≥=+]")

def compare_key(text: str) -> str:
    """Most aggressive comparison form (only for equality checks, never for display):
    N3 + all spaces removed + keep only information-bearing characters.
    Two texts with the same compare_key are considered semantically equal."""
    return "".join(_KEEP_CK.findall(n3(text)))


_LATIN = re.compile(r"[0-9A-Za-zÄÖÜäöüß]")

def garbage_ratio(text: str) -> float:
    """Fraction of 'exotic' characters (broken PDF ToUnicode maps, e.g. Tamil vowel
    signs used as subscript substitutes). High values => formula/encoding fragment."""
    if not text:
        return 0.0
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return 0.0

    def good(c: str) -> bool:
        o = ord(c)
        return (o < 0x0250 or 0x0370 <= o <= 0x03FF or 0x1E00 <= o <= 0x1EFF
                or 0x2000 <= o <= 0x27BF or 0x1D400 <= o <= 0x1D7FF)
    bad = sum(1 for c in visible if not good(c))
    return bad / len(visible)


# ----------------------------------------------------------------- sentence splitter
# Abbreviations after which there is NO sentence end (colleague finding: "bzw." was detected as a sentence end)
_ABBREV = (
    "bzw|z\\. ?B|u\\. ?a|d\\. ?h|i\\. ?d\\. ?R|ggf|evtl|max|min|ca|vgl|Nr|Abs|Kap|Tab|Abb|"
    "inkl|zzgl|u\\. ?U|o\\. ?ä|o\\. ?g|s\\. ?o|s\\. ?u|usw|etc|Hrsg|Aufl|S|f|ff|Pkt|gem|"
    "engl|dt|allg|techn|el|Fa|Ing|Dr|Prof"
)
_ABBR_RE = re.compile(r"(?:\b(?:" + _ABBREV + r")\.)$", re.IGNORECASE)
_SENT_END = re.compile(r"(?<=[.!?:])\s+(?=[A-ZÄÖÜ0-9„\"(])")


def split_sentences(text: str) -> list[str]:
    """Sentence splitter with an abbreviation guard. Operates on N1/N2 text."""
    if not text:
        return []
    parts, out = _SENT_END.split(text), []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out:
            prev = out[-1]
            # Abbreviation at the end of the predecessor, or a number ending in a dot ("Abschnitt 5. 2" etc.)
            if _ABBR_RE.search(prev) or re.search(r"\b\d+\.$", prev):
                out[-1] = prev + " " + p
                continue
        out.append(p)
    return out
