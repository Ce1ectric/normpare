"""
refs.py -- internal and external references.

internal: references to sections/chapters/annexes/tables/figures of the same standard.
external: other standards/regulations (DIN, EN, IEC, VDE-AR, ISO, TAB, laws).
"""
from __future__ import annotations
import re

_INT = re.compile(
    r"\b(?:Abschnitt|Kapitel|Unterabschnitt)\s+(\d+(?:\.\d+)*)"
    r"|\bAnhang\s+([A-Z])(?:\.(\d+(?:\.\d+)*))?"
    r"|\b(?:Tabelle|Bild|Abbildung|Gleichung|Formel)\s+([A-Z]?\.?\d+(?:\.\d+)*)")

_EXT = re.compile(
    r"\b(?:DIN\s+(?:EN\s+)?(?:IEC\s+)?|EN\s+(?:IEC\s+)?|IEC\s+|ISO\s+|CLC/TS\s+|DIN\s+VDE\s+|"
    r"VDE-AR-[NE]\s+|VDE\s+V?\s?|FNN[- ])"
    r"[0-9][0-9\-–:. ]*\d"
    r"|\b(?:EnWG|EEG|KWKG|NAV|MsbG|SysStabV|EltBauV)\b(?:\s+\d{4})?")

_CLEAN_TRAIL = re.compile(r"[,.;:]+$")


def internal(text: str) -> list[str]:
    out = []
    for m in _INT.finditer(text or ""):
        if m.group(1):
            out.append(m.group(1))
        elif m.group(2):
            out.append(m.group(2) + ("." + m.group(3) if m.group(3) else ""))
        elif m.group(4):
            out.append(m.group(0).split()[0] + " " + m.group(4))
    return sorted(set(out))


def external(text: str) -> list[str]:
    out = []
    for m in _EXT.finditer(text or ""):
        ref = _CLEAN_TRAIL.sub("", m.group(0).strip())
        ref = re.sub(r"\s+", " ", ref)
        out.append(ref)
    return sorted(set(out))
