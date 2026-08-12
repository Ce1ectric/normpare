"""The readable side of ``review_removed.json``: the removals worth a second look.

The JSON artifact is for the harness and for tools; this file is for a person. It answers,
without opening any JSON, the three questions a reviewer of a removal report has: why is
this on the list, what did the old edition say, and where does that text stand today.

The list marks, it never filters -- every removal on it is also in the change stream, and
the stream is unchanged by AP-11.
"""
from __future__ import annotations

from pathlib import Path

from ..stages.review_removed import (
    MIN_CHARS,
    OLD_SURPLUS_MIN,
    RELOCATION_TAU,
    REVIEW_REASONS,
    review_entries,
)

#: How many characters of the removed text the list shows. Enough to recognise the span,
#: short enough to keep a page of cases readable.
CASE_CHARS = 200

#: What each reason means, for a reader who has not read AP-09 and AP-10.
REASON_TEXT = {
    "relocated_outside_mapping":
        "Text steht im neuen Dokument, aber außerhalb der zugeordneten Abschnitte — "
        "der Absatz-Aligner konnte ihn nie sehen (Kapitelzuordnung)",
    "missed_inside_mapping":
        "Text steht in einem zugeordneten Abschnitt — die Zuordnung stimmte, "
        "die Paarung misslang (Absatz-Aligner)",
    "unbalanced_mapping":
        f"die Zuordnung bringt mindestens {OLD_SURPLUS_MIN} Alt-Absätze mehr ein, "
        "als sie Neu-Absätze hat (AP-09)",
}


def _titles(new_doc: dict | None) -> dict:
    return {s.get("id"): s.get("title")
            for s in (new_doc or {}).get("sections") or []}


def _shorten(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= CASE_CHARS else text[:CASE_CHARS] + " …"


def _case(index: int, entry: dict, titles: dict) -> list[str]:
    reloc = entry.get("relocation") or {}
    lines = [
        f"## {index}. `{entry['mapping_id']}` — {entry.get('title')}",
        "",
        "- Gründe: " + ", ".join(f"`{r}`" for r in entry["review_reasons"]),
        "- entfallene Absätze: "
        + (", ".join(f"`{i}`" for i in entry["old_ids"]) or "_(keine)_")
        + f" ({entry['chars']} Zeichen)",
        f"- Alt-Überschuss der Zuordnung: {entry['old_surplus']}",
    ]
    if reloc.get("best_score", 0.0) >= RELOCATION_TAU and reloc.get("best_section"):
        section = reloc["best_section"]
        title = titles.get(section)
        lines.append(f"- gefunden in `{section}`"
                     + (f" — {title}" if title else "")
                     + f", Überdeckung {reloc['best_score']:.2f}"
                     + ("" if reloc.get("in_mapping") else " (außerhalb der Zuordnung)"))
    return lines + ["", f"> {_shorten(entry.get('old_text') or '')}", ""]


def render_review_list(entries: list[dict], pair: str, new_doc: dict | None = None) -> str:
    """The markdown text of the review list -- deterministic, derived from ``entries``."""
    titles = _titles(new_doc)
    counts = {r: sum(1 for e in entries if r in e["review_reasons"])
              for r in REVIEW_REASONS}
    lines = [
        f"# Prüfliste entfallener Text — {pair}",
        "",
        f"{len(entries)} Entfallen-Meldungen sind einen zweiten Blick wert. Keine davon "
        "ist aus dem",
        "Änderungsstrom entfernt oder umgewichtet — diese Liste markiert, sie filtert nicht.",
        "",
        "| Grund | Fälle | Bedeutung |",
        "|---|---:|---|",
    ]
    lines += [f"| `{r}` | {counts[r]} | {REASON_TEXT[r]} |" for r in REVIEW_REASONS]
    lines += [
        "",
        f"Schwellen: Überdeckung ≥ {RELOCATION_TAU:.1f} über Wort-Trigramme, "
        f"Alt-Überschuss ≥ {OLD_SURPLUS_MIN}, beurteilt ab {MIN_CHARS} Zeichen.",
        "Mehrere Gründe je Fall sind möglich und stehen alle da; sortiert ist nach Grund, "
        "dann",
        "nach Überdeckung, dann nach Alt-Überschuss.",
        "",
    ]
    if not entries:
        return "\n".join(lines + ["_(keine)_", ""])
    for i, entry in enumerate(entries, 1):
        lines += _case(i, entry, titles)
    return "\n".join(lines)


def build_review_list(synopse: dict, new_doc: dict | None, out_path: str | Path,
                      pair: str) -> Path:
    """Write ``Pruefliste_entfallen_<run>.md`` beside the other deliverables."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review_list(review_entries(synopse), pair, new_doc),
                   encoding="utf-8")
    return out
