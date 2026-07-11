"""Enrichment stage (S2): modality, references and parameter values.

``enrich_doc`` reads a norm_doc.json, adds per-paragraph n1/modality/refs/values and the
per-section aggregates, and writes it back.
"""
from .enrich import enrich_doc

__all__ = ["enrich_doc"]
