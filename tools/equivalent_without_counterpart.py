"""equivalent_without_counterpart.py -- what ``equivalent`` on a pure addition means (AP-38).

``semantic_status: equivalent`` on a change without a counterpart is inadmissible under
the AP-28 rule: there is no earlier statement the text could be equal to. The rule has
stood in the field description since AP-28, it does not work, and the share rose again
(116 / 72 / 63 = 4,7 % / 3,1 % / 3,4 % of the interpretations of the three runs of
2026-09-01). AP-37, 6.4 showed it is not an information problem: not one of the 251
records carries a ``possible_move_to``, and the rule needs nothing but axis A.

The conclusion "then sharpen the rule" is what this tool tests before it is acted on,
because the free texts do not read like one matter but like four:

===============  ==========================================================================
``relocation``   a relocation or adoption with a recognisable counterpart -- ``replaced``
                 would be right, the model is wrong
``torn``         a torn continuation, an extraction artefact -- the *change record* is
                 wrong, and ``equivalent`` is the most honest answer the vocabulary offers
``duplicate``    a duplicate of another change record of the same chapter -- likewise
``genuine``      a genuine addition without any counterpart -- ``extended`` is right and
                 ``equivalent`` is simply the wrong value
===============  ==========================================================================

Sharpened blindly, the rule would hit all four alike and force the two middle groups to
claim a substantive change that the model disputes in the same breath. So the four are
told apart mechanically first, from signals that are on disk.

**The assignment rule**, disclosed here because a number is only worth what its rule is.
Five signals are read per record, and *none of them decides on its own*:

``feedback``           the change index appears in the ``change_indices`` of a
                       ``pipeline_feedback`` entry of the same chapter -- the strongest
                       trace, and the one AP-38 already measured
``names_change``       the free text names another change of the same chapter
                       ("Änderung [2]"), and the chapter really has that index
``names_place``        the free text names a location (Abschnitt, Anhang, Bild, Tabelle,
                       Gleichung, Absatz, Kapitel)
``formal_component``   axis D lies entirely in the formal components
                       (:data:`FORMAL_COMPONENTS`)
``fragment``           the change text is a sentence fragment: it does not end a sentence
                       and stays under :data:`FRAGMENT_WORDS` words

plus the *voice* of the free text (``change`` and ``impact`` together), as three keyword
sets: :data:`VOICE_MOVE`, :data:`VOICE_DUPLICATE`, :data:`VOICE_ARTEFACT`.

The rules are tried in this order, and the first that holds wins::

    duplicate     voice_duplicate and (feedback or names_change)
    relocation    voice_move      and (names_change or names_place)
    torn          voice_artefact  and (feedback or fragment or formal_component)
    genuine       no voice at all, no feedback, and no fragment
    unclassified  everything else

``unclassified`` is counted and reported: a classification that houses everything has
bent itself. How often more than one rule would have held is counted too, so the price of
the order is visible rather than hidden.

The voices are German because the free text of the three runs is German (``language``
defaults to ``de``); a run interpreted in another language would need its own sets.

Read-only, offline: no LLM, no network, not a sentence of standard text interpreted here.
``--dir`` may be given several times (the rule from CLAUDE.md), ``--out`` is the only
thing written and goes through the write guard.

Usage::

    poetry run python tools/equivalent_without_counterpart.py --dir arbeit/4110_v4 \\
        --dir arbeit/4120_v4 --dir arbeit/60909_v3 --out runs/AP-38_2026-09-02
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.stages.deutung import WITHOUT_COUNTERPART, reported_indices

#: The groups, in the order of the report. ``unclassified`` is last and is a result, not
#: a leftover: a rule set that never fails to assign has stopped being a rule set.
GROUPS = ("relocation", "torn", "duplicate", "genuine", "unclassified")

#: The two groups in which the *change record* is what is wrong, not the interpretation.
ARTEFACT_GROUPS = ("torn", "duplicate")

#: Axis-D values that describe the form of the text rather than its substance. A record
#: whose components lie entirely here is formal, which is what torn continuations,
#: caption fragments and heading residue look like.
FORMAL_COMPONENTS = frozenset({"heading", "caption", "reference", "formula", "note"})

#: Under this many words, and without a sentence end, the change text is a fragment.
FRAGMENT_WORDS = 12

#: The free text says the text is now somewhere else, or came from somewhere else.
VOICE_ELSEWHERE = ("verschob", "verschieb", "übernommen", "übernahme", "überführt",
                   "integriert", "ersetzt", "umplatzier", "an anderer stelle",
                   "hierher", "fortgeführt", "umgestellt in")

#: ... that the text is still, or was already, there: the change record claims a change
#: the model disputes. "Keine inhaltliche Änderung" is deliberately **not** among them --
#: every one of these 251 records answers ``equivalent``, so the phrase says nothing.
VOICE_PRESENT = ("inhaltlich unverändert", "inhaltlich identisch", "war aber zuvor",
                 "war zuvor", "war aber bereits", "war bereits", "bereits vorhanden",
                 "bleibt erhalten", "entspricht aber dem alten", "entspricht dem alten",
                 "ist aber teil", "teil der fortgesetzten", "teil der unveränderten",
                 "unverändert vorhanden", "weiterhin vorhanden", "unverändert enthalten")

#: ... of a preprocessing artefact: a torn sentence, formula residue, list formatting.
VOICE_ARTEFACT = ("artefakt", "fragment", "extrakt", "verstümmelt", "platzhalter",
                  "umbruch", "formatierung", "formatänderung", "aufzählungszeichen",
                  "redaktionelle einleitung", "preprocessing", "überbleibsel",
                  "diff-erkennung", "verschluckt", "erkennungsfehler")

#: ... of a duplicate of another change record.
VOICE_DUPLICATE = ("duplikat", "doppelt", "identisch mit", "wiederholung", "zweimal")

#: The model describing the *detection* rather than the standard: "wird als eigener
#: Absatz erkannt", "als neuer Listeneintrag geführt". The verb carries the distinction --
#: erkannt/geführt/gelistet is what the pipeline did, eingefügt/ergänzt what the standard
#: did. Counts as :data:`VOICE_ARTEFACT`.
_DETECTION = re.compile(r"\bals\s+(?:\S+\s+){0,3}?(?:erkannt|gelistet|geführt|gemeldet)\b",
                        re.IGNORECASE)

#: The voices, in the order they are reported.
VOICES = ("voice_elsewhere", "voice_present", "voice_artefact", "voice_duplicate")

#: All signal names, in the order of the report.
SIGNALS = ("feedback", "names_change", "names_place", "formal_component", "fragment",
           *VOICES)

#: The two families the rules below are built from: something claims the change is not a
#: change (``CLAIM``), and the record looks like the artefacts do (``SHAPE``).
ARTEFACT_CLAIM = frozenset({"voice_artefact", "voice_present", "feedback"})
ARTEFACT_SHAPE = frozenset({"feedback", "fragment", "formal_component"})

#: "Änderung [2]", "Änderung 2", "Index 9" -- a pointer at another change record.
_NAMED_CHANGE = re.compile(r"(?:änderung|index)\s*\[?\s*(\d+)", re.IGNORECASE)

#: "Abschnitt 5.4", "Anhang B", "Bild 2", "Tabelle 10", "Gleichung (4)", "Absatz [2]",
#: "Punkt 1)". The place has to carry a number or a single capital letter, otherwise
#: "der Punkt der Aufzählung" would count as a location.
_NAMED_PLACE = re.compile(
    r"(?:[Aa]bschnitt|[Aa]nhang|[Aa]bsatz|[Kk]apitel|[Bb]ild|[Tt]abelle|[Gg]leichung"
    r"|[Uu]nterabschnitt|[Pp]unkt\w*)\s*[\[(]?\s*(?:\d|[A-Z](?:\b|\.))")


def free_text(deutung: dict) -> str:
    """What the model wrote in prose about this change -- ``change`` and ``impact``."""
    return " ".join(str(deutung.get(f) or "") for f in ("change", "impact")).strip()


def change_text(change: dict | None) -> str:
    """The text the change record is about, whichever side carries it."""
    c = change or {}
    return str(c.get("new_text") or c.get("old_text") or "")


def is_fragment(text: str) -> bool:
    """Whether the change text is a sentence fragment rather than a statement."""
    stripped = text.strip()
    if not stripped:
        return True
    return stripped[-1] not in ".!?" and len(stripped.split()) < FRAGMENT_WORDS


def named_changes(text: str, own_index, n_changes: int) -> list[int]:
    """The other change records of this chapter the free text names, in order.

    A number is only a partner if the chapter really has that index -- otherwise
    "Gleichung 11" or a year would count as one.
    """
    seen: list[int] = []
    for match in _NAMED_CHANGE.finditer(text):
        i = int(match.group(1))
        if i != own_index and 0 <= i < n_changes and i not in seen:
            seen.append(i)
    return seen


def signals(deutung: dict, change: dict | None, reported: set,
            n_changes: int) -> tuple[set[str], list[int]]:
    """The signals of one record, and the other changes its free text names."""
    text = free_text(deutung)
    lower = text.lower()
    found = set()
    if deutung.get("change_index") in reported:
        found.add("feedback")
    named = named_changes(text, deutung.get("change_index"), n_changes)
    if named:
        found.add("names_change")
    if _NAMED_PLACE.search(text):
        found.add("names_place")
    components = deutung.get("affected_components") or []
    if components and set(components) <= FORMAL_COMPONENTS:
        found.add("formal_component")
    if is_fragment(change_text(change)):
        found.add("fragment")
    for name, markers in (("voice_elsewhere", VOICE_ELSEWHERE),
                          ("voice_present", VOICE_PRESENT),
                          ("voice_artefact", VOICE_ARTEFACT),
                          ("voice_duplicate", VOICE_DUPLICATE)):
        if any(m in lower for m in markers):
            found.add(name)
    if _DETECTION.search(text):
        found.add("voice_artefact")
    return found, named


def matching_rules(found: set[str]) -> list[str]:
    """Every group whose rule holds -- the first is the verdict, the rest is the price."""
    hits = []
    if "voice_duplicate" in found and found & {"feedback", "names_change"}:
        hits.append("duplicate")
    if "voice_elsewhere" in found and found & {"names_change", "names_place"}:
        hits.append("relocation")
    if (found & ARTEFACT_CLAIM and found & ARTEFACT_SHAPE
            and len(found & (ARTEFACT_CLAIM | ARTEFACT_SHAPE)) >= 2):
        hits.append("torn")
    if not (found & set(VOICES)) and "feedback" not in found:
        hits.append("genuine")
    return [g for g in GROUPS if g in hits]


def classify(found: set[str]) -> str:
    """The group of one record -- ``unclassified`` when no rule holds."""
    for group in ("duplicate", "relocation", "torn", "genuine"):
        if group in matching_rules(found):
            return group
    return "unclassified"


def cause(row: dict) -> str:
    """A coarse cause for a record of the artefact groups -- the basis of the ranking.

    Mechanical: the duplicate says what it is, everything else is named by the leading
    axis-D component and whether the change text is a fragment. It is the ranking a
    package that fixes these in the diff would start from.
    """
    if row["group"] == "duplicate":
        return "duplicate"
    component = (row["components"] or ["(no component)"])[0]
    return f"{component}, {'fragment' if 'fragment' in row['signals'] else 'passage'}"


def rows(chapters: list[dict], synopse: dict) -> list[dict]:
    """One row per ``equivalent`` interpretation on a change without a counterpart."""
    changes = {ch.get("mapping_id"): (ch.get("changes") or [])
               for ch in (synopse.get("chapters") or [])}
    out = []
    for ch in chapters:
        mid = ch.get("mapping_id")
        records = changes.get(mid) or []
        reported = reported_indices(ch)
        for d in (ch.get("interpretations") or ch.get("deutungen") or []):
            if d.get("semantic_status") != "equivalent":
                continue
            if d.get("structural_operation") not in WITHOUT_COUNTERPART:
                continue
            i = d.get("change_index")
            change = records[i] if isinstance(i, int) and 0 <= i < len(records) else None
            found, named = signals(d, change, reported, len(records))
            out.append({"mapping_id": mid, "section_id": ch.get("section_id"),
                        "change_index": i, "kind": (change or {}).get("kind"),
                        "operation": d.get("structural_operation"),
                        "components": d.get("affected_components") or [],
                        "signals": found, "named_changes": named,
                        "rules": matching_rules(found), "group": classify(found),
                        "text": " ".join(change_text(change).split())[:120],
                        "free_text": " ".join(free_text(d).split())[:200]})
    return out


def all_interpretations(chapters: list[dict]) -> list[dict]:
    """Every interpretation of the run -- the denominator of the shares."""
    return [d for ch in chapters
            for d in (ch.get("interpretations") or ch.get("deutungen") or [])]


def classify_run(chapters: list[dict], synopse: dict) -> dict:
    """The classification of one run: the rows, the counts and the counter-checks."""
    flagged = rows(chapters, synopse)
    every = all_interpretations(chapters)
    counts = {g: sum(1 for r in flagged if r["group"] == g) for g in GROUPS}

    # the counter-check the report question asks for: does axis D pile up on the formal
    # components here, or does it only follow the distribution of all changes?
    def components(records) -> Counter:
        return Counter(c for r in records for c in (r.get("affected_components") or []))

    # ... and the upper bound of what a package in the diff could ever collect: how many
    # change records of the run are reported in a pipeline_feedback entry at all
    n_reported = sum(len(reported_indices(ch)) for ch in chapters)
    n_changes = sum(len(ch.get("changes") or [])
                    for ch in (synopse.get("chapters") or []))
    return {
        "rows": flagged, "counts": counts, "n_flagged": len(flagged),
        "n_interpretations": len(every),
        "n_reported_indices": n_reported, "n_changes": n_changes,
        "kinds": Counter(r["kind"] for r in flagged),
        "signal_counts": {s: sum(1 for r in flagged if s in r["signals"])
                          for s in SIGNALS},
        "n_multiple_rules": sum(1 for r in flagged if len(r["rules"]) > 1),
        "multiple_rules": Counter(" + ".join(r["rules"]) for r in flagged
                                  if len(r["rules"]) > 1),
        "components_flagged": components([{"affected_components": r["components"]}
                                          for r in flagged]),
        "components_all": components(every),
        "causes": Counter(cause(r) for r in flagged if r["group"] in ARTEFACT_GROUPS),
    }


def load(out_dir, label: str | None = None) -> dict:
    """One run: the two artefacts and the classification over them.

    Nothing is written back -- the run directory of a finished run is evidence, and a
    tool that edits its evidence measures itself.
    """
    src = Path(out_dir)
    for name in ("synopse.json", "deutung.json"):
        if not (src / name).exists():
            raise SystemExit(f"{src / name} is missing -- the report needs the "
                             f"synopse.json and the deutung.json of a finished run.")
    synopse = json.loads((src / "synopse.json").read_text(encoding="utf-8"))
    deutung = json.loads((src / "deutung.json").read_text(encoding="utf-8"))
    chapters = deutung.get("chapters") or []
    return {"dir": src, "label": label or src.name,
            "run": classify_run(chapters, synopse),
            "digests": {"synopse.json": regression.sha256_file(src / "synopse.json"),
                        "deutung.json": regression.sha256_file(src / "deutung.json")}}


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def _line(label: str, value, whole: int | None = None, indent: str = "      ") -> str:
    width = 44 - len(indent)
    text = f"{indent}{label:<{width}}{value:>8}"
    return text if whole is None else f"{text} = {_pct(value, whole)} %"


def render(run: dict) -> str:
    """The report of one run (``equivalent_without_counterpart_<run>.txt``)."""
    r = run["run"]
    n_all, n_flag = r["n_interpretations"], r["n_flagged"]
    L = [f"equivalent without a counterpart -- {run['dir']}",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {n}: sha256 {s}" for n, s in sorted(run["digests"].items())]
    L += ["",
          _line("interpretations", n_all, indent="  "),
          _line("equivalent without a counterpart", n_flag, n_all, indent="  ")]
    L += [_line(f"on {k}", n, indent="    ")
          for k, n in sorted(r["kinds"].items(), key=lambda kv: (-kv[1], str(kv[0])))]
    L += ["", "1. the four groups (the assignment rule is in the module docstring)"]
    L += [_line(g, r["counts"][g], n_flag) for g in GROUPS]
    L += [_line("of them artefact groups",
                sum(r["counts"][g] for g in ARTEFACT_GROUPS), n_flag, indent="    ")]
    L += ["", "2. the signals (none of them decides on its own)"]
    L += [_line(s, r["signal_counts"][s], n_flag) for s in SIGNALS]
    L += ["", "3. the price of the order: how often two rules would have held",
          _line("more than one rule", r["n_multiple_rules"], n_flag)]
    L += [f"        {combination}: {n}"
          for combination, n in sorted(r["multiple_rules"].items())]
    L += ["", "4. axis D here against axis D everywhere (the counter-check)",
          f"      {'component':<28}{'flagged':>10}{'share':>9}{'all':>10}{'share':>9}"]
    n_c_flag = sum(r["components_flagged"].values())
    n_c_all = sum(r["components_all"].values())
    for component, _n in r["components_all"].most_common():
        a, b = r["components_flagged"][component], r["components_all"][component]
        L.append(f"      {component:<28}{a:>10}{_pct(a, n_c_flag):>8} %"
                 f"{b:>10}{_pct(b, n_c_all):>8} %")
    L += ["", "5. the upper bound for a package in the diff",
          _line("change records reported as an artefact", r["n_reported_indices"],
                r["n_changes"])]
    L += ["", "6. the causes of the artefact cases, most frequent first"]
    L += [_line(c, n) for c, n in sorted(r["causes"].most_common(),
                                         key=lambda kv: (-kv[1], kv[0]))]
    L += ["", "7. the records, by group"]
    for group in GROUPS:
        members = [row for row in r["rows"] if row["group"] == group]
        L.append(f"  {group} ({len(members)})")
        for row in members:
            marks = ",".join(sorted(row["signals"])) or "-"
            L += [f"    {row['mapping_id']} #{row['change_index']} [{marks}]",
                  f"      TEXT: {row['text']}",
                  f"      SAYS: {row['free_text']}"]
    return "\n".join(L) + "\n"


def _row(label: str, cells: list, indent: str = "  ") -> tuple:
    return ("row", indent, label, [str(c) for c in cells])


def _format(lines: list, width: int) -> list[str]:
    label_width = max([40] + [len(i[1]) + len(i[2]) + 2
                              for i in lines if isinstance(i, tuple)])
    out = []
    for item in lines:
        if not isinstance(item, tuple):
            out.append(item)
            continue
        _, indent, label, cells = item
        out.append(f"{indent}{label:<{label_width - len(indent)}}"
                   + "".join(f"{c:>{width}}" for c in cells))
    return out


def render_comparison(runs: list[dict]) -> str:
    """One column per run, in the order of the command line -- the two-corpus rule."""
    width = max(16, max(len(r["label"]) for r in runs) + 2)

    def cells(select) -> list[str]:
        return [str(select(r["run"])) for r in runs]

    L = [f"equivalent without a counterpart -- {len(runs)} runs",
         f"normpare {regression._version()}, recomputed offline (no LLM, no network)"]
    L += [f"  {r['label']}: {r['dir']}, deutung.json sha256 "
          f"{r['digests']['deutung.json'][:16]}..." for r in runs]
    L += ["", _row("", [r["label"] for r in runs]),
          _row("interpretations", cells(lambda r: r["n_interpretations"])),
          _row("equivalent without a counterpart",
               cells(lambda r: f"{r['n_flagged']} = "
                     f"{_pct(r['n_flagged'], r['n_interpretations'])} %"))]
    L += ["", "1. the four groups"]
    L += [_row(g, cells(lambda r, g=g: f"{r['counts'][g]} = "
                        f"{_pct(r['counts'][g], r['n_flagged'])} %"), indent="      ")
          for g in GROUPS]
    L += [_row("artefact groups together",
               cells(lambda r: f"{sum(r['counts'][g] for g in ARTEFACT_GROUPS)} = "
                     f"{_pct(sum(r['counts'][g] for g in ARTEFACT_GROUPS), r['n_flagged'])} %"),
               indent="      ")]
    L += ["", "2. the signals"]
    L += [_row(s, cells(lambda r, s=s: f"{r['signal_counts'][s]} = "
                        f"{_pct(r['signal_counts'][s], r['n_flagged'])} %"),
               indent="      ") for s in SIGNALS]
    L += ["", "3. the price of the order",
          _row("more than one rule", cells(lambda r: r["n_multiple_rules"]),
               indent="      ")]
    L += ["", "4. axis D: share here / share over all interpretations of the run"]
    components = sorted({c for r in runs for c in r["run"]["components_all"]})
    for component in components:
        L.append(_row(component, [
            f"{_pct(r['run']['components_flagged'][component], sum(r['run']['components_flagged'].values()))}"
            f" / {_pct(r['run']['components_all'][component], sum(r['run']['components_all'].values()))} %"
            for r in runs], indent="      "))
    L += ["", "5. the upper bound for a package in the diff",
          _row("change records reported as an artefact",
               cells(lambda r: f"{r['n_reported_indices']} = "
                     f"{_pct(r['n_reported_indices'], r['n_changes'])} %"),
               indent="      ")]
    L += ["", "6. the causes of the artefact cases"]
    causes = sorted({c for r in runs for c in r["run"]["causes"]},
                    key=lambda c: (-sum(r["run"]["causes"][c] for r in runs), c))
    L += [_row(c, cells(lambda r, c=c: r["causes"][c]), indent="      ")
          for c in causes]
    return "\n".join(_format(L, width)) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, action="append",
                    help="run output directory to read (repeatable: the runs are then "
                         "compared column by column, in the order given)")
    ap.add_argument("--out", default=None, help="directory to write the report to "
                                                "(default: print only)")
    args = ap.parse_args(argv)

    dest = regression.guard_write(Path(args.out)) if args.out else None
    runs = [load(d) for d in args.dir]
    reports = [(f"equivalent_without_counterpart_{r['dir'].name}.txt", render(r))
               for r in runs]
    if len(runs) > 1:
        reports.append(("equivalent_without_counterpart_comparison.txt",
                        render_comparison(runs)))
    for _name, text in reports:
        print(text)

    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in reports:
            path = regression.guard_write(dest / name)
            path.write_text(text, encoding="utf-8")
            print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
