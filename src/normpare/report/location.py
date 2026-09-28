"""Where a change really stands -- read once for every view that shows one (AP-41).

A chapter mapping is named after its head, and that name is right for the block: it says
which chapters of the two editions belong together. It is wrong for a single change inside
it. Measured over the three reference runs, 1008 of 2474 changes at 4110, 902 of 2321 at
4120 and 307 of 1899 at 60909 stand in a subsection of the head they were shown under --
four out of ten readers looking a change up started in the wrong place.

The section itself is pipeline property and comes from ``synopse.json``
(``section_old``/``section_new``, built in :mod:`normpare.stages.diff`). This module only
decides what to *show*: nothing where the section is the head, the section where it is
not, and both sides where they differ from each other.
"""
from __future__ import annotations


def change_location(change: dict, head: str | None) -> str:
    """The section of this change when that is not where it is shown, else ``""``.

    ``"11.2.5 → 11.2.6.7"`` when the paragraph changed section between the editions,
    ``"11.2.6.7"`` when only one side is known or both agree, ``""`` when the change
    stands exactly where its block title says. A run written before AP-41 carries neither
    field and gets the empty string, not a guess.
    """
    old_section, new_section = change.get("section_old"), change.get("section_new")
    if old_section and new_section and old_section != new_section:
        return f"{old_section} → {new_section}"
    return section_location(new_section or old_section, head)


def section_location(section: str | None, head: str | None) -> str:
    """The same rule for a record that knows one section, e.g. ``kennwert_changes``."""
    return section if section and section != head else ""
