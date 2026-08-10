# Synthetic corpus TR-X 1000

A fictional technical rule in two editions, written to exercise every mechanic normpare
has to handle. **It contains no text from any real standard** and is therefore part of the
distributed package — unlike `tests/fixtures/korpus/`, which stays local.

That distinction is not a convenience. A PyPI package containing VDE-AR-N full text would
not be publishable, so a synthetic corpus is a precondition for the release goal, not a
shortcut for CI.

## Files

| File | Role | Written by |
|---|---|---|
| `alt.md` | edition 2020-01 | Cowork |
| `neu.md` | edition 2026-01 | Cowork |
| `erwartung.toml` | what each mapping and each sentence **must** yield | Cowork |
| `alt.docx`, `neu.docx` | generated from the Markdown, pipeline input | generated |
| `deutungen.json` | frozen interpretations for `FixtureProvider` | Cowork, after the first run |

`erwartung.toml` is the specification. **A night run never edits it.** If the pipeline
disagrees with it, that is a finding for the report — the expectation is the reference,
the implementation is what gets adjusted.

## What the corpus covers

Structural mechanics: unchanged, changed, renumbered, moved, merged, added, removed.
Chapter `4.2` (old) becomes `4.3` (new) with identical wording because a new `4.2` was
inserted — a mapping that follows numbering alone lands on the wrong chapter here.

Normative direction: tightening (`sollte` → `muss`, 5 % → 2 %), a new requirement, a
removed requirement, and one deliberately mixed case. Chapter `4.1` shortens the interval
from yearly to half-yearly *and* adds an exception — tightening for most, loosening for a
subset. A flat taxonomy has to pick one and is then wrong for the other; the expected
value is `gemischt`.

German normative constructions: synthetic modal infinitives (`ist nachzuweisen`,
`hat sicherzustellen`), modal verbs, `dürfen` with and without negation, and two
indicative sentences — one carrying an obligation, one purely descriptive. The second is
the control: a classifier that treats every indicative as a requirement fails it.

Evidence checking: a quote whose fragments are all present, one where a fragment is
invented, one where only the first 15 characters match while the sentence reverses the
statement, and one that is fully present but occurs in several change records.

## Regenerating the DOCX

```bash
poetry run python tools/build_synthetic.py
```

Reads `alt.md` and `neu.md`, writes `alt.docx` and `neu.docx`. The Markdown is the source
of truth; the DOCX are derived and may be regenerated at any time.

Markdown conventions: `##` and `###` start a section, the first token after the marker is
the section number. `- ` starts a list item. Everything else is a paragraph.

The section number is *not* written into the heading text -- Word numbers headings
automatically, and the reader counts them from the heading levels. The builder checks the
numbers in the Markdown against the numbering the reader will derive and refuses to write
a document where the two disagree.

## The reference run

The deterministic stages over this corpus are the tracked regression baseline (ENT-27):
no standard text, so it works in CI.

```bash
poetry run python tools/build_synthetic.py
poetry run normpare compare --old tests/fixtures/synthetic/alt.docx \
    --new tests/fixtures/synthetic/neu.docx --out baselines/synthetic --no-llm
poetry run python tools/regression.py --dir baselines/synthetic          # check
poetry run python tools/regression.py --dir baselines/synthetic --update # refresh
```

`baselines/synthetic.json` is tracked, the run directory `baselines/synthetic/` is not --
it is reproduced byte for byte from the Markdown by the three commands above.
