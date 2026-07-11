"""Human-readable summary of a ``norm_doc.json`` via the domain model.

Lets you follow what the model captured from the ingest output: document metadata,
section/paragraph counts, the modality distribution, and example paragraphs that carry
parameter values (the high-value "Kennwerte"). Used by ``normpare inspect PATH``.

Works at every pipeline stage: at ingest time the enrichment fields (``n1``, ``modality``,
``values`` ...) are not present yet, so they are shown as empty / "not enriched".
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .model import NormDocument


def load(path: str | Path) -> NormDocument:
    """Load a ``norm_doc.json`` file into a :class:`NormDocument`."""
    return NormDocument.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _as_list(value: Any) -> list:
    """Return the value if it is a list, else an empty list (enrichment may be absent)."""
    return value if isinstance(value, list) else []


def render_summary(nd: NormDocument, max_sections: int = 20, max_examples: int = 8) -> str:
    """Render a compact, readable overview of a document as a string."""
    paragraphs = [p for _, p in nd.iter_paragraphs()]
    n_tables = sum(len(s.tables) for s in nd.sections)
    n_figures = sum(len(s.figures) for s in nd.sections)
    n_formulas = sum(len(s.formulas) for s in nd.sections)
    n_values = sum(len(_as_list(p.values)) for p in paragraphs)
    modality = Counter(
        (p.modality if isinstance(p.modality, str) else "(not enriched)") for p in paragraphs
    )

    lines: list[str] = [
        f"Document : {nd.doc_id}  (source: {nd.source})",
        f"Title    : {nd.title}",
        f"Version  : {nd.version_label}",
        "",
        f"Sections : {len(nd.sections)}    Paragraphs: {len(paragraphs)}",
        f"Assets   : {n_tables} tables, {n_figures} figures, {n_formulas} formulas",
        f"Values   : {n_values} parameter value(s)",
        "Modality : " + ", ".join(f"{k}={v}" for k, v in modality.most_common()),
        "",
        f"Sections (first {max_sections}):",
    ]
    for s in nd.sections[:max_sections]:
        title = str(s.title)[:58]
        lines.append(f"  [L{s.level}] {str(s.id):12s} {title:58s} paras={len(s.paragraphs)}")

    with_values = [(sec, p) for sec, p in nd.iter_paragraphs() if _as_list(p.values)]
    lines += ["", f"Example paragraphs with parameter values (up to {max_examples} of "
                  f"{len(with_values)}):"]
    for sec, p in with_values[:max_examples]:
        text_source = p.n1 if isinstance(p.n1, str) else p.n0  # n1 only exists post-enrichment
        text = " ".join(str(text_source).split())[:88]
        lines.append(f"  {sec.id}/{p.id}  [{p.modality}]")
        lines.append(f"      text  : {text}")
        lines.append(f"      values: {', '.join(str(v.raw) for v in _as_list(p.values))}")
        refs = _as_list(p.refs_external)
        if refs:
            lines.append(f"      refs  : {', '.join(refs)}")
    return "\n".join(lines)


def inspect_path(path: str | Path) -> str:
    """Convenience: load a ``norm_doc.json`` and return its rendered summary."""
    return render_summary(load(path))
