"""The four axes of ``deutung.json``, read once for every view that shows them.

AP-14 put the axes into the interpretation, AP-16 closed their vocabularies -- and until
AP-17 they appeared in no output document at all. Three views show them now (the change
CSV, the component view, and a short marker in the synopsis and the HTML), and all three
read the interpretation through this module, so "how an axis is read" is decided in one
place.

Nothing here interprets: the values are taken as they stand in the run. A run written
before AP-14 carries none of them, which is a legitimate state and not an error -- every
function below falls back to "nothing said" rather than to a default value.
"""
from __future__ import annotations

#: The three axes the model fills in. Axis A (``structural_operation``) is derived by the
#: pipeline from the change kind and is deliberately not part of the marker: it repeats
#: what the change record already shows.
MODEL_AXES = ("semantic_status", "normative_direction", "affected_components")

#: All five axis fields an interpretation can carry -- the marker uses three of them, the
#: CSV all five.
AXIS_FIELDS = ("structural_operation", "semantic_status", "normative_direction",
               "affected_components", "indeterminate_reason")


def carries_axes(deutung: dict | None) -> bool:
    """Whether this run knows the axes at all (AP-14 and later).

    Decided on the presence of the *fields*, not of values: an interpretation whose axes
    were all rejected by the validator still belongs to a run that carries them.
    """
    for chapter in (deutung or {}).get("chapters", []) or []:
        for interpretation in chapter.get("interpretations") or []:
            if any(axis in interpretation for axis in MODEL_AXES):
                return True
    return False


def _components(interpretation: dict) -> list[str]:
    """Axis D as a list of strings, in the delivered order ("most important first")."""
    values = interpretation.get("affected_components")
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, str) and v]


def axis_marker(interpretation: dict) -> str:
    """The three model axes as one short marker, e.g. ``[narrowed · relaxed · scope]``.

    Empty when the interpretation carries none of them -- a run without axes gets no
    marker rather than an empty pair of brackets.
    """
    parts = [interpretation.get(axis) or ""
             for axis in ("semantic_status", "normative_direction")]
    parts.append(", ".join(_components(interpretation)))
    parts = [p for p in parts if p]
    return f"[{' · '.join(parts)}]" if parts else ""


def chapter_index(synopse: dict | None) -> tuple[dict, dict]:
    """The synopse chapters by mapping id (ENT-24) and, as the fallback, by section id."""
    by_mapping: dict = {}
    by_section: dict = {}
    for chapter in (synopse or {}).get("chapters", []) or []:
        if chapter.get("mapping_id"):
            by_mapping.setdefault(chapter["mapping_id"], chapter)
        for key in (chapter.get("new_id"), chapter.get("old_id")):
            if key:
                by_section.setdefault(key, chapter)
    return by_mapping, by_section


def _text(value) -> str:
    """A schema value as text -- ``None`` and missing become the empty string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def change_rows(synopse: dict | None, deutung: dict | None) -> list[dict]:
    """One flat row per interpretation, in the order of ``deutung.json``.

    The row carries the axes, the two older labels and the facts the change record
    contributes (its kind, the chapter title). Missing fields become empty strings;
    ``affected_components`` stays a list, so every view can decide how to join it.

    The order is the run's own -- chapters as interpreted, changes as delivered. It is
    reproducible without sorting, and sorting by section id would only look ordered
    (``"10"`` before ``"2"``).
    """
    by_mapping, by_section = chapter_index(synopse)
    rows: list[dict] = []
    for chapter in (deutung or {}).get("chapters", []) or []:
        section_id = chapter.get("section_id")
        source = (by_mapping.get(chapter.get("mapping_id"))
                  or by_section.get(section_id) or {})
        changes = source.get("changes") or []
        for interpretation in chapter.get("interpretations") or []:
            index = interpretation.get("change_index")
            change = {}
            if isinstance(index, int) and 0 <= index < len(changes):
                change = changes[index]
            rows.append({
                "section_id": _text(section_id),
                "mapping_id": _text(chapter.get("mapping_id") or source.get("mapping_id")),
                "chapter_title": _text(source.get("title")),
                "change_index": _text(index),
                "change_kind": _text(change.get("kind")),
                "structural_operation": _text(interpretation.get("structural_operation")),
                "semantic_status": _text(interpretation.get("semantic_status")),
                "normative_direction": _text(interpretation.get("normative_direction")),
                "affected_components": _components(interpretation),
                "indeterminate_reason": _text(interpretation.get("indeterminate_reason")),
                "semantic_label": _text(interpretation.get("semantic_label")),
                "obligation": _text(interpretation.get("obligation")),
                "change": _text(interpretation.get("change")),
                "impact": _text(interpretation.get("impact")),
                "evidence": _text(interpretation.get("evidence")),
                "evidence_ok": _text(interpretation.get("evidence_ok")),
                "confidence": _text(interpretation.get("confidence")),
                # AP-41, last in the row: where the change really stands. Appended rather
                # than sorted in, so an evaluation that reads the CSV by column position
                # keeps working.
                "section_old": _text(change.get("section_old")),
                "section_new": _text(change.get("section_new")),
            })
    return rows
