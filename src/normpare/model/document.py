"""Domain model for a normative document -- the ingest output (``norm_doc.json``).

Rich, typed objects over the exact per-stage JSON schema. ``from_dict``/``to_dict``
round-trip the v2 ``norm_doc.json`` losslessly (dict equality). That round-trip is the
regression guarantee against the verified reference implementation:
the persisted JSON stays schema-identical to v2.

The schema grows across stages: ingest writes the base fields, enrichment adds modality,
references and parameter values, the keyword stage adds section keywords. Fields that are
absent at a given stage are simply not emitted, so an object round-trips at *every* stage.

Design notes:
- Base keys (always present from ingest) are plain fields and are always emitted -- even
  when their value is ``None``.
- Stage-added keys default to the ``_MISSING`` sentinel and are emitted only when they
  were present in the source (distinct from a present ``None`` value).
- Any key not modelled explicitly is captured in ``extra`` and re-emitted unchanged, so
  the round-trip never loses data even if a future corpus adds fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Sentinel for "key was absent in the source" (distinct from a present ``None`` value).
_MISSING: Any = object()


def _emit(out: dict, key: str, value: Any) -> None:
    """Add ``key`` to ``out`` only if the value is not the absent-sentinel."""
    if value is not _MISSING:
        out[key] = value


@dataclass
class Value:
    """A parsed parameter value (see ``stages/enrich/values.py``)."""

    op: Any
    value: Any
    unit: Any
    base_value: Any
    base_unit: Any
    raw: Any
    value2: Any = _MISSING
    base_value2: Any = _MISSING
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: dict) -> "Value":
        d = dict(src)
        obj = cls(
            op=d.pop("op", None), value=d.pop("value", None), unit=d.pop("unit", None),
            base_value=d.pop("base_value", None), base_unit=d.pop("base_unit", None),
            raw=d.pop("raw", None),
            value2=d.pop("value2", _MISSING), base_value2=d.pop("base_value2", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"op": self.op, "value": self.value, "unit": self.unit,
               "base_value": self.base_value, "base_unit": self.base_unit, "raw": self.raw}
        _emit(out, "value2", self.value2)
        _emit(out, "base_value2", self.base_value2)
        out.update(self.extra)
        return out


@dataclass
class Table:
    """A section-level table (cell matrix + caption)."""

    id: Any
    caption: Any
    cells: Any
    n_cols: Any
    n_rows: Any
    para_anchor: Any
    page: Any = _MISSING
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: dict) -> "Table":
        d = dict(src)
        obj = cls(
            id=d.pop("id", None), caption=d.pop("caption", None), cells=d.pop("cells", None),
            n_cols=d.pop("n_cols", None), n_rows=d.pop("n_rows", None),
            para_anchor=d.pop("para_anchor", None), page=d.pop("page", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"id": self.id, "caption": self.caption, "cells": self.cells,
               "n_cols": self.n_cols, "n_rows": self.n_rows, "para_anchor": self.para_anchor}
        _emit(out, "page", self.page)
        out.update(self.extra)
        return out


@dataclass
class Figure:
    """A section-level figure (caption + image asset path)."""

    id: Any
    caption: Any
    image_path: Any
    para_anchor: Any
    page: Any = _MISSING
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: dict) -> "Figure":
        d = dict(src)
        obj = cls(
            id=d.pop("id", None), caption=d.pop("caption", None),
            image_path=d.pop("image_path", None), para_anchor=d.pop("para_anchor", None),
            page=d.pop("page", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"id": self.id, "caption": self.caption, "image_path": self.image_path,
               "para_anchor": self.para_anchor}
        _emit(out, "page", self.page)
        out.update(self.extra)
        return out


@dataclass
class Formula:
    """A section-level formula (LaTeX + linear form)."""

    id: Any
    latex: Any
    linear: Any
    para_anchor: Any
    image_path: Any = _MISSING
    table_anchor: Any = _MISSING
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: dict) -> "Formula":
        d = dict(src)
        obj = cls(
            id=d.pop("id", None), latex=d.pop("latex", None), linear=d.pop("linear", None),
            para_anchor=d.pop("para_anchor", None),
            image_path=d.pop("image_path", _MISSING), table_anchor=d.pop("table_anchor", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"id": self.id, "latex": self.latex, "linear": self.linear,
               "para_anchor": self.para_anchor}
        _emit(out, "image_path", self.image_path)
        _emit(out, "table_anchor", self.table_anchor)
        out.update(self.extra)
        return out


@dataclass
class Paragraph:
    """A single paragraph with its base fields and (stage-dependent) enrichment.

    Ingest writes ``id``, ``n0``, ``kind``, ``tc``, ``formulas`` and ``figures``; the
    enrichment stage adds ``n1``, ``modality``, ``modality_counts``, ``refs_internal``,
    ``refs_external`` and ``values``. Absent fields are not emitted, so a paragraph
    round-trips at every stage.

    ``formulas`` and ``figures`` are lists of asset-id strings (references to the
    section-level assets), not nested objects. ``values`` are parsed :class:`Value`
    objects. ``tc`` holds tracked-change counts ``{"ins": int, "del": int}``.
    """

    id: Any
    n0: Any
    kind: Any
    tc: Any
    formulas: Any
    figures: Any
    n1: Any = _MISSING
    modality: Any = _MISSING
    modality_counts: Any = _MISSING
    refs_internal: Any = _MISSING
    refs_external: Any = _MISSING
    values: Any = _MISSING  # list[Value] when present
    page: Any = _MISSING
    term_no: Any = _MISSING
    synthetic_title: Any = _MISSING
    extra: dict = field(default_factory=dict)

    def is_normative(self) -> bool:
        """True if the paragraph carries a binding modality (not merely informative)."""
        return self.modality not in (None, "informativ", _MISSING)

    @classmethod
    def from_dict(cls, src: dict) -> "Paragraph":
        d = dict(src)
        values = d.pop("values", _MISSING)
        obj = cls(
            id=d.pop("id", None), n0=d.pop("n0", None), kind=d.pop("kind", None),
            tc=d.pop("tc", None), formulas=d.pop("formulas", None), figures=d.pop("figures", None),
            n1=d.pop("n1", _MISSING), modality=d.pop("modality", _MISSING),
            modality_counts=d.pop("modality_counts", _MISSING),
            refs_internal=d.pop("refs_internal", _MISSING),
            refs_external=d.pop("refs_external", _MISSING),
            values=([Value.from_dict(v) for v in values] if values is not _MISSING else _MISSING),
            page=d.pop("page", _MISSING), term_no=d.pop("term_no", _MISSING),
            synthetic_title=d.pop("synthetic_title", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"id": self.id, "n0": self.n0, "kind": self.kind, "tc": self.tc,
               "formulas": self.formulas, "figures": self.figures}
        _emit(out, "n1", self.n1)
        _emit(out, "modality", self.modality)
        _emit(out, "modality_counts", self.modality_counts)
        _emit(out, "refs_internal", self.refs_internal)
        _emit(out, "refs_external", self.refs_external)
        if self.values is not _MISSING:
            out["values"] = [v.to_dict() for v in self.values]
        _emit(out, "page", self.page)
        _emit(out, "term_no", self.term_no)
        _emit(out, "synthetic_title", self.synthetic_title)
        out.update(self.extra)
        return out


@dataclass
class Section:
    """A chapter/section with its paragraphs and section-level assets.

    Ingest writes ``id``, ``title``, ``level``, ``part`` and the asset lists; enrichment
    adds ``modality_counts`` and the reference lists, the keyword stage adds ``keywords``.
    """

    id: Any
    title: Any
    level: Any
    part: Any
    paragraphs: list[Paragraph]
    tables: list[Table]
    figures: list[Figure]
    formulas: list[Formula]
    modality_counts: Any = _MISSING
    refs_internal: Any = _MISSING
    refs_external: Any = _MISSING
    keywords: Any = _MISSING
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: dict) -> "Section":
        d = dict(src)
        obj = cls(
            id=d.pop("id", None), title=d.pop("title", None), level=d.pop("level", None),
            part=d.pop("part", None),
            paragraphs=[Paragraph.from_dict(p) for p in d.pop("paragraphs", [])],
            tables=[Table.from_dict(t) for t in d.pop("tables", [])],
            figures=[Figure.from_dict(fg) for fg in d.pop("figures", [])],
            formulas=[Formula.from_dict(fm) for fm in d.pop("formulas", [])],
            modality_counts=d.pop("modality_counts", _MISSING),
            refs_internal=d.pop("refs_internal", _MISSING),
            refs_external=d.pop("refs_external", _MISSING),
            keywords=d.pop("keywords", _MISSING),
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out: dict = {"id": self.id, "title": self.title, "level": self.level, "part": self.part}
        _emit(out, "keywords", self.keywords)
        out["paragraphs"] = [p.to_dict() for p in self.paragraphs]
        out["tables"] = [t.to_dict() for t in self.tables]
        out["figures"] = [fg.to_dict() for fg in self.figures]
        out["formulas"] = [fm.to_dict() for fm in self.formulas]
        _emit(out, "modality_counts", self.modality_counts)
        _emit(out, "refs_internal", self.refs_internal)
        _emit(out, "refs_external", self.refs_external)
        out.update(self.extra)
        return out


@dataclass
class NormDocument:
    """A whole normative document (one version): metadata plus its sections."""

    doc_id: Any
    title: Any
    version_label: Any
    source: Any
    sections: list[Section]
    extra: dict = field(default_factory=dict)

    def iter_paragraphs(self):
        """Yield ``(section, paragraph)`` for every paragraph in document order."""
        for section in self.sections:
            for paragraph in section.paragraphs:
                yield section, paragraph

    def find_section(self, section_id: str) -> "Section | None":
        """Return the section with the given id, or ``None``."""
        return next((s for s in self.sections if s.id == section_id), None)

    @classmethod
    def from_dict(cls, src: dict) -> "NormDocument":
        d = dict(src)
        obj = cls(
            doc_id=d.pop("doc_id", None), title=d.pop("title", None),
            version_label=d.pop("version_label", None), source=d.pop("source", None),
            sections=[Section.from_dict(s) for s in d.pop("sections", [])],
        )
        obj.extra = d
        return obj

    def to_dict(self) -> dict:
        out = {"doc_id": self.doc_id, "title": self.title, "version_label": self.version_label,
               "source": self.source, "sections": [s.to_dict() for s in self.sections]}
        out.update(self.extra)
        return out
