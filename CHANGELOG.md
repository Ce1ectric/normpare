# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-07-09

First usable release. `normpare` compares two editions of a technical standard and produces
the change set deterministically, with an optional AI-interpreted synopsis on top.

!!! note
    0.1.0 was withdrawn immediately after upload and must not be used.

### Added

- One-call Python API `normpare.compare(old, new, out_dir, ...)`, a `normpare compare`
  CLI (`--old/--new/--out`, or `--config`), and `python -m normpare`.
- Deterministic pipeline, carried over unchanged from a verified reference implementation:
  ingest (PDF/DOCX) → enrich → chapter mapping → paragraph alignment → synopsis →
  keywords → report. JSON is written per stage; behaviour is byte-reproducible.
- A typed domain model (`NormDocument` and friends) over the algorithms; the per-stage JSON
  is round-trip tested against the model.
- Outputs: an annotated HTML document of the new version (marked changes, per-chapter
  summaries, embedded figures and cell-level table diffs), a **deterministic** synopsis
  (`.docx`) and an AI-interpreted **final** synopsis (`.docx`) that bundles several change
  lines into one readable entry, tables exported as CSV, and machine-readable JSON.
- LLM interpretation via Anthropic, OpenAI, local Ollama, or a copy-paste chat workflow.
  Answers are cached per model + prompt, and `--batch` submits all uncached chapters as one
  Anthropic message batch (~50 % cheaper). Every quoted piece of evidence is checked against
  the source text (`evidence_ok`), and anything unverified lands in a review queue.
- Optional embedding rescue pass (ADR-0001), off by default: within each chapter it
  re-pairs residual `removed`/`new` paragraphs that were reworded in place, using a German
  embedding model with a reproducible on-disk vector cache. Enable with `--embed-fallback`
  (needs the `embeddings` extra); threshold `tau_embed` (default `0.82`).
- Non-normative fragment filter (ADR-0002): short glossary/heading/abbreviation fragments
  are kept out of the "removed" stream.
- `normpare inspect PATH` for a readable digest of an ingested document.
- Documentation site (mkdocs) and CI/CD for the three target platforms
  (Linux, macOS, Windows) on Python 3.13; PyPI publishing via Trusted Publishing on tag.

### Notes

- Requires Python 3.13.
- Determinism and reproducibility are treated as core properties: set-iteration order was
  fixed so repeated runs produce byte-identical mapping, keywords and statistics, and a
  content-identical synopsis.

[0.1.1]: https://github.com/Ce1ectric/normpare/releases/tag/v0.1.1
