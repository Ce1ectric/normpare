"""Exception hierarchy for normpare.

Every deliberately raised error derives from :class:`NormpareError` so callers can catch
them specifically.
"""
from __future__ import annotations


class NormpareError(Exception):
    """Base class for all normpare errors."""


class ConfigError(NormpareError):
    """Invalid or incomplete configuration."""


class MissingInputError(NormpareError):
    """A stage was called without its required input (no silent continuation)."""


class IngestError(NormpareError):
    """Failure while reading a source document (DOCX/PDF)."""


class LlmError(NormpareError):
    """Failure in the LLM / interpretation layer."""
