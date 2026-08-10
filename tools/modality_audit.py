"""modality_audit.py -- count the deontic constructions of a standard, sentence by sentence.

Modality decides whether a passage is read as a duty or as background. The classifier in
``stages/enrich/modality.py`` knows six modal verbs and one infinitive construction; German
expresses necessity in more ways than that. This tool measures how often each construction
occurs and how the *current* classifier labels it -- one number per construction instead of
an impression.

Seven constructions are told apart:

===============================  ==================================  =================
key                              example                             acted upon
===============================  ==================================  =================
``infinitiv_synthetisch_sein``   "ist nachzuweisen"                  AP-05
``infinitiv_synthetisch_haben``  "hat sicherzustellen"               AP-05
``infinitiv_analytisch``         "ist ... zu pruefen"                already covered
``duerfen_negiert``              "darf nicht ueberschreiten"         AP-05 (prohibition)
``muessen_negiert``              "muss nicht", "braucht nicht"       AP-05 (duty drops)
``lexikalisch``                  "verpflichtet", "bedarf der ..."    counted only
``indikativ_pflicht``            "stellt sicher, dass"               counted only
===============================  ==================================  =================

The patterns here are *measurement definitions* and deliberately not imported from
``modality.py``: the audit has to stay able to say "this sentence carries construction X
and the classifier still calls it informativ". Run after a change, the same report is the
independent check that the classifier now covers what the audit counts.

The synthetic infinitive is counted in two readings, because AP-00 used the looser one:

``form``      the word form alone ("nachzuweisen"), no matter what governs it -- this is
              the AP-00 reading (1243 sentences, 1028 of them informativ)
``regiert``   the form together with a governing ``sein``/``haben``, i.e. the actual modal
              infinitive. A purpose clause ("um die Grenzwerte einzuhalten") carries the
              form but not the duty, and is only in the first reading.

A greedy control pattern (``\\w+zu\\w+en``) is counted as well: it shows how many nouns a
pattern without the separable-prefix constraint would drag in ("Bezugsspannungen",
"Kurzunterbrechungen").

Part two is the ``soll`` census for ENT-23: every ``soll``/``sollen`` sentence sorted by
what stands next to it (proof duty, threshold, deadline, rest). It classifies nothing --
it is material for a decision, not a decision.

Only ever reads the source directory; every output goes to ``--out``, and
``regression.guard_write`` refuses any target below ``out/``.

Usage::

    python tools/modality_audit.py --doc out/4110_hot/alt/norm_doc.json \\
        --out runs/AP-05_2026-08-10 --name 4110_alt
    python tools/modality_audit.py --dir out/4110_hot --out runs/AP-05_2026-08-10 \\
        --name 4110 --soll
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ -- for regression
sys.path.insert(0, str(ROOT / "src"))

import regression

from normpare.stages.enrich.modality import RANK, classify_sentence
from normpare.stages.enrich.values import extract_values
from normpare.text.textnorm import split_sentences

#: Constructions in report order, with the note whether AP-05 acts on them.
CONSTRUCTIONS = {
    "infinitiv_synthetisch_sein": "sein + zu-infix -- AP-05: becomes a requirement",
    "infinitiv_synthetisch_haben": "haben + zu-infix -- AP-05: becomes a requirement",
    "infinitiv_analytisch": "ist/sind ... zu <verb> -- already covered, counted only",
    "duerfen_negiert": "negated permission -- prohibition, kept apart in AP-05",
    "muessen_negiert": "negated necessity -- a duty falls away, kept apart in AP-05",
    "lexikalisch": "deontic noun or adjective -- counted only, no package yet",
    "indikativ_pflicht": "present indicative carrying a duty -- counted only, no package yet",
}

# -- the constructions ----------------------------------------------------------------------

#: Separable prefixes that can carry the ``zu`` infix. A closed class -- that is what keeps
#: the pattern off nouns like ``Bezugsspannungen`` (be + zu + ...), whose ``zu`` sits at a
#: syllable boundary and whose prefix is not separable.
SEPARABLE_PREFIXES = (
    "ab|acht|an|auf|aufrecht|aus|bei|bereit|dar|durch|ein|einher|empor|entgegen|entlang|"
    "fern|fest|fort|frei|gegen|gegenüber|gleich|gut|heim|her|herab|heran|herauf|heraus|"
    "herbei|herein|herunter|hervor|hin|hinab|hinauf|hinaus|hinein|hinterher|hinzu|hoch|"
    "instand|klar|kurz|los|mit|nach|nahe|nieder|offen|preis|sicher|stand|statt|still|"
    "teil|tief|über|um|unter|voll|vor|voran|voraus|vorbei|wahr|weg|weiter|wett|wieder|"
    "zu|zurecht|zurück|zusammen"
)

#: Verbs whose stem already ends in ``-end``. Only for them is a form ending in ``-enden``
#: an infinitive ("anzuwenden"); for every other verb it is the attributive gerundive
#: ("die anzuschließenden Anlagen"), which describes rather than obliges.
_STEM_IN_END = r"wenden|senden|enden|blenden|spenden|schänden"

#: prefix(es) + ``zu`` + stem + infinitive ending. Lower case only: a capital first letter
#: means a noun (``Auszubildenden``), and a synthetic infinitive practically never opens a
#: sentence in a standard.
SYNTHETIC_INFINITIVE = (rf"\b(?:{SEPARABLE_PREFIXES}){{1,2}}"
                        rf"zu(?:{_STEM_IN_END}|(?!\w*enden\b)[a-zäöüß]{{2,}}?e[lr]?n)\b")

_AUX_SEIN = r"ist|sind|war|waren|sei|seien|wäre|wären"
_AUX_HABEN = r"hat|haben|hatte|hatten|hätte|hätten"

#: Conjunctions that introduce an infinitive of their own: an aim ("um ... einzuhalten"),
#: its absence ("ohne ... einzuhalten") or a substitution ("statt ... einzuhalten"). None of
#: them obliges anyone, so they cut the link between an auxiliary and the infinitive.
_OTHER_GOVERNOR = r"\bum\b|\bohne\b|\b(?:an)?statt\b"

#: What may stand between the auxiliary and the infinitive. The sentence is the outer
#: bound (the text is already split into sentences), a semicolon separates clauses, and no
#: other governor may intervene. Deliberately not length-bounded: measured on 4110, the
#: auxiliary of a duty stands up to 400 characters away, behind relative clauses and
#: parentheses, and a length bound loses those duties (runs/AP-05_2026-08-10/regierung_probe.txt).
_WINDOW = rf"(?:(?!{_OTHER_GOVERNOR})[^;])*?"

#: Verb-final clauses put the auxiliary behind the infinitive ("die nachzuweisen sind"),
#: possibly after a coordinated second infinitive -- but never across a comma, which would
#: reach into the next clause.
_TAIL = rf"(?:(?!{_OTHER_GOVERNOR})[^,;])*?"


def _governed(aux: str) -> re.Pattern:
    """``aux ... infinitive`` (main clause) or ``infinitive ... aux`` (verb-final clause)."""
    return re.compile(
        rf"\b(?i:{aux})\b{_WINDOW}{SYNTHETIC_INFINITIVE}"
        rf"|{SYNTHETIC_INFINITIVE}{_TAIL}\s(?i:{aux})\b")


P_SYNTH_FORM = re.compile(SYNTHETIC_INFINITIVE)
P_SYNTH_SEIN = _governed(_AUX_SEIN)
P_SYNTH_HABEN = _governed(_AUX_HABEN)

#: Control: the same idea without the prefix constraint -- everything it finds beyond
#: :data:`P_SYNTH_FORM` is a false hit.
P_SYNTH_GREEDY = re.compile(r"\b\w+zu\w+en\b", re.I)

P_ANALYTIC = re.compile(r"\b(hat zu|haben zu)\b|\b(ist|sind)\b[^.;]{0,80}?\bzu\s+\w+en\b", re.I)
P_DUERFEN_NEG = re.compile(r"\b(darf|dürfen)\b[^.;]{0,200}?\bnicht\b"
                           r"|\bunzulässig\b|\bnicht zulässig\b", re.I)
P_MUESSEN_NEG = re.compile(r"\b(muss|müssen|braucht|brauchen)\b[^.;]{0,60}?\bnicht\b"
                           r"|\bnicht\b[^.;]{0,30}?\berforderlich\b"
                           r"|\bentfällt\b[^.;]{0,30}?\b(pflicht|nachweis)", re.I)
P_LEXICAL = re.compile(r"\bverpflicht\w*|\b(bedarf|bedürfen)\b|\b\w*pflicht\w*\b"
                       r"|\bobliegt\b|\buntersagt\b|\bverboten\b"
                       r"|\bVoraussetzung\b|\bzuständig\b", re.I)
#: Deliberately narrow: verbs of the performative present indicative that a standard uses to
#: assign an action to a party. A lower bound, meant for sizing a follow-up package.
P_INDICATIVE = re.compile(
    r"\b(stellt|stellen) sicher\b|\b(legt|legen)\b[^.;]{0,60}?\bfest\b"
    r"|\b(meldet|melden|veranlasst|veranlassen|beauftragt|dokumentiert|protokolliert"
    r"|informiert|übermittelt|übermitteln|teilt mit|prüft|prüfen)\b", re.I)

_DETECTORS = {
    "infinitiv_synthetisch_sein": P_SYNTH_SEIN.search,
    "infinitiv_synthetisch_haben": P_SYNTH_HABEN.search,
    "infinitiv_analytisch": P_ANALYTIC.search,
    "duerfen_negiert": P_DUERFEN_NEG.search,
    "muessen_negiert": P_MUESSEN_NEG.search,
    "lexikalisch": P_LEXICAL.search,
    "indikativ_pflicht": P_INDICATIVE.search,
}

# -- the soll census (ENT-23) ---------------------------------------------------------------

#: Census groups in priority order -- a sentence lands in the first one that matches.
SOLL_GROUPS = ("mit_nachweis", "mit_schwellwert", "mit_frist", "sonstiges")

P_SOLL = re.compile(r"\b(soll|sollen)\b", re.I)
P_NACHWEIS = re.compile(r"\bnachweis\w*|\bnachzuweisen\b|\bvorzulegen\b|\bnachgewiesen\b"
                        r"|\bnachweispflicht\w*|\bbeizubringen\b|\bvorzuweisen\b", re.I)
P_FRIST = re.compile(r"\bfrist\w*|\bintervall\w*|\bspätestens\b|\bunverzüglich\b"
                     r"|\binnerhalb\b|\bjährlich\b|\bhalbjährlich\b|\bmonatlich\b"
                     r"|\bwöchentlich\b|\btäglich\b|\bturnus\w*|\bvorab\b"
                     r"|\b\d+\s*(tage?n?|wochen?|monate?n?|jahre?n?)\b", re.I)


def soll_group(sentence: str) -> str:
    if P_NACHWEIS.search(sentence):
        return "mit_nachweis"
    if extract_values(sentence):
        return "mit_schwellwert"
    if P_FRIST.search(sentence):
        return "mit_frist"
    return "sonstiges"


# -- walking a document ---------------------------------------------------------------------

def sentences(doc: dict):
    """``(section id, paragraph id, sentence)`` for every sentence of a ``norm_doc.json``.

    Reads the N1 form -- the same text the productive enrichment classifies.
    """
    for sec in doc.get("sections") or []:
        for par in sec.get("paragraphs") or []:
            text = par.get("n1") or par.get("n0") or ""
            for sentence in split_sentences(text):
                if sentence.strip():
                    yield sec.get("id"), par.get("id"), sentence.strip()


def _example(sec_id, par_id, sentence, label) -> dict:
    return {"kapitel": sec_id, "absatz": par_id, "klasse": label, "satz": sentence}


def audit(doc: dict, examples_per_construction: int = 20) -> dict:
    """Count every construction and how the current classifier labels it."""
    counts = Counter()
    labels = {key: Counter() for key in CONSTRUCTIONS}
    examples: dict[str, list[dict]] = {key: [] for key in CONSTRUCTIONS}
    informative_examples: dict[str, list[dict]] = {key: [] for key in CONSTRUCTIONS}
    label_totals = Counter()
    total = 0
    form_only = {"gesamt": 0, "informativ": 0, "ungovernt": 0}
    greedy_extra: list[str] = []

    for sec_id, par_id, sentence in sentences(doc):
        total += 1
        label = classify_sentence(sentence)
        label_totals[label] += 1

        governed = False
        for key, detect in _DETECTORS.items():
            if not detect(sentence):
                continue
            counts[key] += 1
            labels[key][label] += 1
            if key.startswith("infinitiv_synthetisch"):
                governed = True
            if len(examples[key]) < examples_per_construction:
                examples[key].append(_example(sec_id, par_id, sentence, label))
            if label == "informativ" and len(informative_examples[key]) < examples_per_construction:
                informative_examples[key].append(_example(sec_id, par_id, sentence, label))

        if P_SYNTH_FORM.search(sentence):
            form_only["gesamt"] += 1
            if label == "informativ":
                form_only["informativ"] += 1
            if not governed:
                form_only["ungovernt"] += 1
        for hit in P_SYNTH_GREEDY.findall(sentence):
            if not P_SYNTH_FORM.fullmatch(hit.lower()) and len(greedy_extra) < 4000:
                greedy_extra.append(hit)

    synthetic = counts["infinitiv_synthetisch_sein"] + counts["infinitiv_synthetisch_haben"]
    synthetic_info = (labels["infinitiv_synthetisch_sein"]["informativ"]
                      + labels["infinitiv_synthetisch_haben"]["informativ"])
    return {
        "saetze": total,
        "klassen": dict(label_totals.most_common()),
        "counts": {key: counts[key] for key in CONSTRUCTIONS},
        "klassenverteilung": {key: dict(labels[key].most_common()) for key in CONSTRUCTIONS},
        "beispiele": examples,
        "beispiele_informativ": informative_examples,
        "synthetisch_regiert": synthetic,
        "synthetisch_regiert_informativ": synthetic_info,
        "synthetisch_form": form_only,
        "gierig_zusatztreffer": dict(Counter(greedy_extra).most_common(30)),
        "gierig_zusatztreffer_gesamt": len(greedy_extra),
    }


def soll_census(doc: dict, examples_per_group: int = 10) -> dict:
    counts = Counter()
    labels = {g: Counter() for g in SOLL_GROUPS}
    examples: dict[str, list[dict]] = {g: [] for g in SOLL_GROUPS}
    for sec_id, par_id, sentence in sentences(doc):
        if not P_SOLL.search(sentence):
            continue
        group = soll_group(sentence)
        counts[group] += 1
        labels[group][classify_sentence(sentence)] += 1
        if len(examples[group]) < examples_per_group:
            examples[group].append(_example(sec_id, par_id, sentence,
                                            classify_sentence(sentence)))
    return {"counts": {g: counts[g] for g in SOLL_GROUPS},
            "klassen": {g: dict(labels[g].most_common()) for g in SOLL_GROUPS},
            "beispiele": examples,
            "gesamt": sum(counts.values())}


# -- report -----------------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:6.2f} %" if whole else "     -- "


def _distribution(dist: dict[str, int]) -> str:
    order = sorted(dist, key=lambda label: -RANK.get(label, 0))
    return ", ".join(f"{label} {dist[label]}" for label in order) or "(none)"


def render(result: dict, src: str, digest: str) -> str:
    total = result["saetze"]
    lines = [
        f"modality construction audit -- {src}",
        f"normpare {regression._version()}, norm_doc.json {digest}",
        "",
        f"sentences: {total}",
        f"current class distribution: {_distribution(result['klassen'])}",
        "",
        "construction                    count    share   today informativ   thereof",
        "-" * 92,
    ]
    for key, note in CONSTRUCTIONS.items():
        n = result["counts"][key]
        info = result["klassenverteilung"][key].get("informativ", 0)
        lines.append(f"  {key:<28}{n:>6}  {_pct(n, total)}   {info:>6} "
                     f"{_pct(info, n)}   {note}")
    lines += [
        "-" * 92,
        "",
        (f"synthetic modal infinitive, governed by sein/haben: "
         f"{result['synthetisch_regiert']} sentences, "
         f"{result['synthetisch_regiert_informativ']} of them informativ today"),
        (f"synthetic form regardless of what governs it (AP-00 reading): "
         f"{result['synthetisch_form']['gesamt']} sentences, "
         f"{result['synthetisch_form']['informativ']} of them informativ today, "
         f"{result['synthetisch_form']['ungovernt']} without a governing sein/haben"),
        (f"greedy control pattern \\w+zu\\w+en: "
         f"{result['gierig_zusatztreffer_gesamt']} hits beyond the prefix-constrained "
         f"pattern -- the words a pattern without the closed prefix class would drag in:"),
    ]
    for word, n in result["gierig_zusatztreffer"].items():
        lines.append(f"    {word}  ({n})")
    if not result["gierig_zusatztreffer"]:
        lines.append("    (none)")

    for key in CONSTRUCTIONS:
        lines += ["", "=" * 92,
                  f"{key} -- {result['counts'][key]} sentences, "
                  f"classes: {_distribution(result['klassenverteilung'][key])}",
                  "=" * 92]
        pool = result["beispiele_informativ"][key] or result["beispiele"][key]
        note = ("twenty sentences the classifier calls informativ today"
                if result["beispiele_informativ"][key] else "twenty sentences")
        lines.append(f"({note})")
        for ex in pool:
            lines.append(f"  [{ex['klasse']:<10}] {ex['kapitel']} / {ex['absatz']}")
            lines.append(f"    {ex['satz']}")
        if not pool:
            lines.append("  (none)")
    return "\n".join(lines) + "\n"


def render_soll(census: dict, sources: dict[str, str]) -> str:
    total = census["gesamt"]
    lines = [
        "soll census (ENT-23) -- how VDE-AR-N actually uses 'soll'/'sollen'",
        f"normpare {regression._version()}",
        *(f"  {name:<22} {digest}" for name, digest in sources.items()),
        "",
        "Groups are assigned in this order; a sentence lands in the first one that fits:",
        "  mit_nachweis     Nachweis / nachzuweisen / vorzulegen / nachgewiesen in the sentence",
        "  mit_schwellwert  a number with a unit (values.extract_values, the productive parser)",
        "  mit_frist        deadline, interval or a period of time",
        "  sonstiges        everything else",
        "",
        f"'soll'/'sollen' sentences: {total}",
        "",
        "  group              count    share    class distribution today",
    ]
    for group in SOLL_GROUPS:
        n = census["counts"][group]
        lines.append(f"  {group:<18}{n:>5}   {_pct(n, total)}   "
                     f"{_distribution(census['klassen'][group])}")
    lines += ["", "Nothing here is reclassified: 'soll' keeps rank 3 until ENT-23 is decided."]
    for group in SOLL_GROUPS:
        lines += ["", "=" * 92, f"{group} -- ten full sentences", "=" * 92]
        for ex in census["beispiele"][group]:
            lines.append(f"  [{ex['klasse']:<10}] {ex['kapitel']} / {ex['absatz']}")
            lines.append(f"    {ex['satz']}")
        if not census["beispiele"][group]:
            lines.append("  (none)")
    return "\n".join(lines) + "\n"


# -- driver -------------------------------------------------------------------------------------

def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"{path} is missing -- the audit needs a norm_doc.json.")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(dest: Path, name: str, text: str) -> Path:
    path = regression.guard_write(dest / name)
    path.write_text(text, encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="run output directory; audits alt/ and neu/")
    ap.add_argument("--doc", help="a single norm_doc.json")
    ap.add_argument("--out", required=True, help="destination directory for the reports")
    ap.add_argument("--name", required=True, help="report name, e.g. 4110")
    ap.add_argument("--suffix", default="", help="appended to every file name, e.g. _nachher")
    ap.add_argument("--soll", action="store_true", help="also write the soll census")
    args = ap.parse_args(argv)

    if bool(args.dir) == bool(args.doc):
        raise SystemExit("give either --dir or --doc")
    dest = regression.guard_write(Path(args.out))
    dest.mkdir(parents=True, exist_ok=True)

    if args.doc:
        editions = {args.name: Path(args.doc)}
    else:
        editions = {f"{args.name}_{side}": Path(args.dir) / side / "norm_doc.json"
                    for side in ("alt", "neu")}

    written, docs = [], {}
    for name, path in editions.items():
        doc = _load(path)
        docs[name] = (doc, path)
        result = audit(doc)
        digest = regression.sha256_file(path)
        text = render(result, str(path), digest)
        written.append(_write(dest, f"modality_audit_{name}{args.suffix}.txt", text))
        payload = {"quelle": str(path), "sha256": digest,
                   "normpare_version": regression._version(),
                   **{k: v for k, v in result.items() if k != "beispiele"}}
        written.append(_write(dest, f"modality_audit_{name}{args.suffix}.json",
                              json.dumps(payload, ensure_ascii=False, indent=1) + "\n"))
        print(f"{name}: {result['saetze']} sentences, "
              f"synthetic governed {result['synthetisch_regiert']} "
              f"({result['synthetisch_regiert_informativ']} informativ), "
              f"form only {result['synthetisch_form']['gesamt']} "
              f"({result['synthetisch_form']['informativ']} informativ)")

    if args.soll:
        merged = {"sections": [s for doc, _ in docs.values()
                               for s in doc.get("sections") or []]}
        census = soll_census(merged)
        sources = {name: regression.sha256_file(path) for name, (_, path) in docs.items()}
        written.append(_write(dest, f"soll_zensus{args.suffix}.txt",
                              render_soll(census, sources)))
        written.append(_write(dest, f"soll_zensus{args.suffix}.json",
                              json.dumps({"quellen": sources, **census},
                                         ensure_ascii=False, indent=1) + "\n"))
        print(f"soll: {census['gesamt']} sentences {census['counts']}")

    for path in written:
        print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
