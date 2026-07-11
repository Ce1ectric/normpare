"""Rich domain model: a typed layer over the per-stage JSON schema."""
from .document import (
    Figure,
    Formula,
    NormDocument,
    Paragraph,
    Section,
    Table,
    Value,
)

__all__ = [
    "NormDocument",
    "Section",
    "Paragraph",
    "Table",
    "Figure",
    "Formula",
    "Value",
]
