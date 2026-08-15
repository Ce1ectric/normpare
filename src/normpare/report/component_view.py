"""The changes grouped by affected component -- the skeleton of the training material.

Every other output is ordered by chapter, because that is how the standard is read. This
one is ordered by axis D, because that is how a course is planned: "what changed about
the proofs to be supplied" is one section here and 238 scattered paragraphs everywhere
else.

Written for a person who builds slides from it and never opens a JSON file. An
interpretation that names several components appears in each of their sections -- the
axis is multi-valued and hiding that would misrepresent it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..stages.deutung import AFFECTED_COMPONENTS, NORMATIVE_DIRECTIONS, OTHER_COMPONENT
from .axes import carries_axes, change_rows

#: How many characters of the evidence the view shows -- enough to recognise the span,
#: short enough to keep a section of cases readable (the convention of
#: :mod:`normpare.report.review_list`).
EVIDENCE_CHARS = 200

#: The directions the overview table counts, in vocabulary order. ``indeterminate`` is
#: not a column: it is not a direction but the refusal to name one, and it is in the CSV.
COUNTED_DIRECTIONS = [d for d in NORMATIVE_DIRECTIONS if d != "indeterminate"]

#: The two buckets after the closed vocabulary: the labelled escape hatch, and the
#: interpretations that name no component at all (older runs, or an axis the validator
#: emptied). They are kept apart from ``none``, which is an explicit statement.
OTHER_SECTION = OTHER_COMPONENT
NO_COMPONENT = "(ohne Komponentenangabe)"

_MISSING_AXES_NOTE = (
    "> **Dieser Lauf führt die vier Achsen nicht** (Fassungen vor AP-14). Die Übersicht "
    "bleibt deshalb leer; alle Deutungen stehen unter „ohne Komponentenangabe“. Ein "
    "neuer Deutungslauf füllt die Abschnitte."
)

#: Two ways to be empty, and they are not the same finding: a run without an
#: interpretation stage has nothing to group, a run before AP-14 has something to group
#: and no axis to group it by.
_NO_INTERPRETATION_NOTE = (
    "> **Dieser Lauf enthält keine Deutung** (Vergleich ohne KI-Lauf). Die Abschnitte "
    "füllen sich, sobald die Deutungsstufe gelaufen ist."
)


def _shorten(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= EVIDENCE_CHARS else text[:EVIDENCE_CHARS] + " …"


def _buckets(rows: list[dict]) -> dict[str, list[dict]]:
    """Every section with its rows, in the order the view renders them.

    A row appears under each component it names; a free ``other:`` value puts it into the
    one ``other:`` section, whatever the label says.
    """
    buckets: dict[str, list[dict]] = {c: [] for c in AFFECTED_COMPONENTS}
    buckets[OTHER_SECTION] = []
    buckets[NO_COMPONENT] = []
    for row in rows:
        components = row.get("affected_components") or []
        if not components:
            buckets[NO_COMPONENT].append(row)
            continue
        # dict.fromkeys: a component named twice (or two free labels) is one section entry
        for key in dict.fromkeys(
                OTHER_SECTION if c.startswith(OTHER_COMPONENT) else c for c in components):
            if key in buckets:
                buckets[key].append(row)
    return buckets


def _overview(buckets: dict[str, list[dict]]) -> list[str]:
    header = " | ".join(COUNTED_DIRECTIONS)
    lines = [f"| Komponente | Änderungen | {header} |",
             "|---|---:|" + "---:|" * len(COUNTED_DIRECTIONS)]
    for name, rows in buckets.items():
        counts = [sum(1 for r in rows if r["normative_direction"] == d)
                  for d in COUNTED_DIRECTIONS]
        label = f"`{name}`" if name != NO_COMPONENT else f"_{name}_"
        lines.append(f"| {label} | {len(rows)} | "
                     + " | ".join(str(c) for c in counts) + " |")
    return lines


def _free_values(rows: list[dict]) -> list[str]:
    """The free ``other:`` labels of the section with their frequency, sorted by name."""
    counts: dict[str, int] = {}
    for row in rows:
        for component in row.get("affected_components") or []:
            if component.startswith(OTHER_COMPONENT):
                counts[component] = counts.get(component, 0) + 1
    return [f"`{name}` ({counts[name]})" for name in sorted(counts)]


def _entry(row: dict) -> list[str]:
    """One change: where it stands, what it does, and the sentence it is read from."""
    title = row["chapter_title"]
    head = f"Kapitel {row['section_id']}" + (f" — {title}" if title else "")
    mark = " · ".join(v for v in (row["normative_direction"], row["semantic_status"]) if v)
    lines = [f"- **{head}**" + (f" · `{mark}`" if mark else ""),
             f"  {row['change']}" if row["change"] else "  _(ohne Beschreibung)_"]
    if row["evidence"]:
        lines.append(f"  > {_shorten(row['evidence'])}")
    return lines + [""]


def render_component_view(rows: list[dict], pair: str, date: str,
                          carries: bool = True) -> str:
    """The markdown text of the component view -- deterministic, derived from ``rows``.

    ``date`` is passed in rather than read from the clock, so the same run renders to the
    same bytes twice.
    """
    buckets = _buckets(rows)
    entries = sum(len(v) for v in buckets.values())
    lines = [
        f"# Änderungen nach Normkomponente — {pair}",
        "",
        f"Stand: {date} · {len(rows)} Deutungen · {entries} Einträge",
        "",
        "Gegliedert nach betroffener Normkomponente (Achse D), nicht nach Kapiteln. Eine "
        "Deutung, die",
        "mehrere Komponenten nennt, steht in jedem dieser Abschnitte — die Achse ist "
        "mehrwertig, und",
        "eine Zuordnung auf nur eine Komponente wäre eine Deutung, die niemand getroffen "
        "hat.",
        "Die Reihenfolge der Abschnitte ist die des Wertevorrats und bleibt damit zwischen "
        "Läufen gleich.",
        "",
    ]
    if not rows:
        lines += [_NO_INTERPRETATION_NOTE, ""]
    elif not carries:
        lines += [_MISSING_AXES_NOTE, ""]
    lines += _overview(buckets) + [""]

    for name, section_rows in buckets.items():
        label = f"`{name}`" if name != NO_COMPONENT else "ohne Komponentenangabe"
        lines += [f"## {label}", ""]
        if name == OTHER_SECTION:
            free = _free_values(section_rows)
            lines += ["Freie Werte: " + (", ".join(free) if free else "_(keine)_"), ""]
        if not section_rows:
            lines += ["_(keine)_", ""]
            continue
        lines += [f"{len(section_rows)} Änderungen.", ""]
        for row in section_rows:
            lines += _entry(row)
    return "\n".join(lines)


def build_component_view(synopse: dict | None, deutung: dict | None,
                         out_path: str | Path, pair: str,
                         date: str | None = None) -> Path:
    """Write ``Aenderungen_nach_Komponente_<run>.md`` beside the other deliverables."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_component_view(change_rows(synopse, deutung), pair,
                              # UTC, like ``manifest.json`` -- the one value not derived
                              # from the run, which is why it can be passed in
                              date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                              carries=carries_axes(deutung)),
        encoding="utf-8")
    return out
