"""The caption rule, kept in one place.

Two readers and one comparison ask the same question about a caption: where does the
number end and where does the description begin? Until AP-39 each of them answered it
with its own regular expression, and the PDF reader's answer was the loosest of the
three -- it accepted a *missing* separator, so the tail of a sentence
("... werden die Einstellwerte nach Tabelle 10 empfohlen.") was read as the caption of
the table next to it and displaced the real one.

The rule this module holds is the one :func:`normpare.stages.diff.caption_text` already
wrote down in prose: a space is not a separator. It is stated once here, so that the
reader and the comparison cannot drift apart again:

* a real separator (``-``, ``--``, ``:``) right after the number always opens a caption;
* without one, what follows must not look like the continuation of a sentence. A
  description starts like a description -- upper case, or a bracket, as in the caption of
  a continuation page ("Tabelle 1 (2 von 2)") -- while a sentence carried on in lower
  case ("Tabelle 10 empfohlen.") or in punctuation ("Bild 11).") is not one.
"""
from __future__ import annotations

import re

#: The characters that may separate a caption's number from its description.
SEPARATOR = r"[–—\-:]"

_SEPARATOR_AFTER_NUMBER = re.compile(r"^\s*" + SEPARATOR)
#: A description begins in upper case or opens a bracket; anything else continues a
#: sentence that started before the number.
_DESCRIPTION_START = re.compile(r"^\s*[A-ZÄÖÜ(\[„“\"]")


def is_caption_rest(rest: str | None) -> bool:
    """True if ``rest`` -- what follows the number of a caption -- belongs to a caption."""
    text = rest or ""
    return bool(_SEPARATOR_AFTER_NUMBER.match(text) or _DESCRIPTION_START.match(text))
