"""
keywords.py -- keyword taxonomy with per-chapter assignment.

Curated term lists per keyword (extensible); assignment via hit density on N3 text
(hits per 1000 words + a title bonus).
Output: keywords.json  {keyword: [{id, title, score}]}, plus a per-section "keywords" list.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from ..text.textnorm import n3

TAXONOMY: dict[str, list[str]] = {
    "Schutztechnik": ["schutz", "schutzeinrichtung", "schutzkonzept", "schutzprüfung",
                      "auslösezeit", "anregung", "distanzschutz", "differentialschutz",
                      "umz", "amz", "kurzschlussschutz", "überstrom", "schutzrelais",
                      "entkupplungsschutz"],
    "Entkupplung/Q-U-Schutz": ["entkupplungsschutz", "q-u-schutz", "qu-schutz",
                               "entkupplung", "kuppelschalter", "üeks", "eks"],
    "Blindleistung/Spannungshaltung": ["blindleistung", "blindstrom", "cos", "verschiebungsfaktor",
                                       "spannungshaltung", "q(u)", "q-regelung", "kennlinie",
                                       "spannungsregelung", "blindarbeit"],
    "Wirkleistungssteuerung": ["wirkleistung", "wirkleistungsreduktion", "leistungsreduzierung",
                               "einspeisemanagement", "p(f)", "leistungsgradient",
                               "sollwertvorgabe", "regelbarkeit"],
    "Dynamische Netzstützung/FRT": ["dynamische netzstützung", "frt", "fault ride",
                                    "fehlerdurchfahrung", "spannungseinbruch", "blindstromeinspeisung",
                                    "kurzschlussstrom", "durchfahren"],
    "Netzanschluss/Anschlusskriterien": ["netzanschlusspunkt", "anschlusskriterien", "netzanschluss",
                                         "anschlussnehmer", "verknüpfungspunkt", "anschlusskapazität",
                                         "netzverträglichkeit", "anschlussleistung"],
    "Anschlussprozess/Unterlagen": ["anschlussanmeldung", "netzverträglichkeitsprüfung",
                                    "inbetriebsetzung", "anschlusszusage", "antragstellung",
                                    "datenblatt", "vordruck", "unterlagen"],
    "Zertifizierung/Nachweise": ["zertifikat", "anlagenzertifikat", "einheitenzertifikat",
                                 "komponentenzertifikat", "konformitätserklärung", "nachweis",
                                 "zertifizierung", "betriebserlaubnisverfahren", "prototyp"],
    "Messwesen/Zähler": ["messung", "messeinrichtung", "zähler", "messstellenbetrieb",
                         "messwandler", "abrechnungsmessung", "lastgang", "smart meter"],
    "Erdung/Sternpunktbehandlung": ["erdung", "erdungsanlage", "sternpunkt", "sternpunktbehandlung",
                                    "erdschluss", "erdkurzschluss", "berührungsspannung",
                                    "erder", "fundamenterder", "potentialausgleich"],
    "Netzrückwirkungen/Power Quality": ["netzrückwirkung", "oberschwingung", "flicker", "unsymmetrie",
                                        "spannungsänderung", "spannungsqualität", "zwischenharmonische",
                                        "kommutierung", "verzerrung", "thd"],
    "Inselbetrieb/Schwarzstart": ["inselbetrieb", "inselnetz", "schwarzstart", "netzwiederaufbau",
                                  "eigenbedarf", "abfangen"],
    "Speicher": ["speicher", "batteriespeicher", "energiespeicher", "ladezustand", "mischanlage"],
    "Ladeeinrichtungen/E-Mobilität": ["ladeeinrichtung", "elektrofahrzeug", "ladepunkt",
                                      "e-mobilität", "ladeinfrastruktur"],
    "Übergabestation/Schaltanlage": ["übergabestation", "schaltanlage", "schaltfeld", "station",
                                     "transformator", "mittelspannungsschaltanlage", "kurzschlussfestigkeit"],
    "Betrieb/Störung": ["betriebsführung", "störung", "instandhaltung", "verfügungsbereich",
                        "schalthoheit", "betriebsverantwortung", "wartung"],
    "Kommunikation/Datenaustausch": ["fernwirktechnik", "informationsaustausch", "kommunikation",
                                     "prozessdaten", "fernsteuerung", "datenübertragung",
                                     "netzbetreiberschnittstelle", "parametrierung"],
    "Frequenzverhalten": ["frequenz", "über-/unterfrequenz", "frequenzänderung", "p(f)",
                          "frequenzhaltung", "rocof", "netzfrequenz"],
}


def assign_keywords(doc: dict, out_path: str | Path | None = None,
                    min_score: float = 1.2) -> dict:
    kw_map: dict[str, list[dict]] = {k: [] for k in TAXONOMY}
    for sec in doc["sections"]:
        if sec["part"] == "vorspann":
            continue
        text = n3(sec["title"] + " " + " ".join(p.get("n1", p.get("n0", ""))
                                                for p in sec["paragraphs"]))
        n_words = max(len(text.split()), 1)
        title_l = n3(sec["title"])
        sec_kw = []
        for kw, terms in TAXONOMY.items():
            hits = sum(text.count(t) for t in terms)
            title_hit = any(t in title_l for t in terms)
            score = hits / n_words * 1000 + (5.0 if title_hit else 0.0)
            if score >= min_score and hits >= 2 or title_hit:
                sec_kw.append((kw, round(score, 2)))
                kw_map[kw].append({"id": sec["id"], "title": sec["title"],
                                   "score": round(score, 2)})
        sec["keywords"] = [k for k, _ in sorted(sec_kw, key=lambda x: -x[1])]
    for kw in kw_map:
        kw_map[kw].sort(key=lambda r: -r["score"])
    if out_path:
        Path(out_path).write_text(json.dumps(kw_map, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return kw_map
