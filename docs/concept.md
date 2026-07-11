# Concept

normpare compares two editions of a technical standard and produces a change synopsis —
**deterministically**, plus an optional **AI interpretation**.

The guiding principle is **facts first, interpretation second**. Everything that can be
established mechanically — which paragraphs correspond to each other, which words changed,
which limit values moved, whether a "should" became a "shall" — is computed by deterministic
algorithms and is reproducible across runs. Only then, and only if you ask for it, does a
language model interpret those established facts. The model never decides *what* changed; it
explains changes that were already found, and it must quote the source text for every
statement it makes.

## Pipeline stages

A run executes these stages in order; each writes a JSON artifact to the output directory,
so runs are inspectable and resumable.

| Stage | What it does |
|---|---|
| **ingest** | read old and new document (PDF via table of contents, DOCX via styles) into a common `norm_doc.json` (sections, paragraphs, tables, figures, formulas, provenance) |
| **enrich** | per paragraph: normalization layers N0–N3, modality per sentence, internal/external references, parameter values (number + unit as a decimal) |
| **map** | align chapters old ↔ new (id+title, then title/content similarity, incl. split/merge) |
| **align** | align paragraphs within mapped chapters; detect moved / split / merged |
| **synopse** | the change diff — syntactic (exact character/token) and semantic (N3) levels, parameter-value changes, modality shifts, table and formula diffs |
| **keywords** | assign a curated keyword taxonomy per chapter |
| **deutung** | per-chapter AI interpretation (summaries, per-change meaning and impact) with an evidence guard — optional, skipped with `--no-llm` |
| **report** | build the deliverables (below) |

## The evidence guard

Every interpretation the model produces must include a short **verbatim quote** from the old
or the new text. After the model answers, normpare checks that this quote actually occurs in
the source. The result is stored per entry as `evidence_ok`, and anything that fails the
check — or that the model itself flagged as contradictory — lands in a **review queue** in
`deutung.json`.

This turns model quality into something you can measure rather than trust: a strong model
verifies at well over 90 %, a weak one collapses because it paraphrases instead of quoting.
See [LLM providers](providers.md#choosing-a-model-measure-do-not-guess).

## Outputs (in the target directory)

- `annotiert_<run>.html` — the new version with colour-marked changes, chapter summaries,
  cell-diffed tables and embedded figures.
- `Synopse_deterministisch_<run>.docx` — tabular old ↔ new change synopsis (always).
- `Synopse_final_<run>.docx` — the AI-interpreted, human-readable per-chapter synopsis that
  bundles several change lines into one entry (only with an AI run).
- `Aenderungen_<run>.pptx` — a training slide draft of the key changes.
- `synopse.json`, `chapters.json`, `statistics.json`, `keywords.json`, `mapping.json` —
  machine-readable intermediates.
- `alt/` and `neu/` — the per-version `norm_doc.json` plus `assets/` (images, and each table
  as a CSV under `assets/tables/`).
