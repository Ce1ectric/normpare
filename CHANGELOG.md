# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Every chapter mapping carries a `mapping_id`: both sides of the mapping, each sorted,
  written as `new<old` — `4.3<4.2` for a renumbering, `6<6.1+6.2` for a merge, `4+4.2<4`
  for a split, `10<` for an addition, `<11` for a removal. `cid = new_id or old_id` names
  a chapter, not a mapping: it drops one side of every merge and split, and two chapters
  that ingest gave the same synthetic id collapse onto one key. Beyond three sources per
  side the list is cut and a digest of the full list appended; a repeated section id gets
  its ordinal appended (`anhang_informativ#2`). Over the 4110 pair all 220 mappings get a
  distinct id, the longest is 51 characters and none needs an ordinal.
  The field is additive — `old_id`, `new_id`, `old_ids` and `new_ids` all stay, and `cid`
  remains derivable.
- The interpretation stage sets the key of an answer itself instead of taking it from the
  answer. The model is asked to echo `section_id`, and in the 4110 run it did not: it
  wrote a slug of the heading (`kapitel_2_normative_verweisungen`) or, for unnumbered
  annexes, the value of the `Teil` field (`anhang_informativ`, five times). 165
  interpretations — 10.6 % — had no chapter left to be checked against. With the key
  written by the stage, all 1552 are checkable; 142 of the 165 pass the evidence guard,
  23 enter the review queue they never reached before.

### Changed

- The 4110 regression baseline points at a replay of the reference run, not at the frozen
  July run itself. The frozen run never changes and the code does, so every approved
  improvement moved the two further apart and was reported as a regression; a harness that
  turns red on every improvement stops being read. The frozen run keeps its own job — the
  historical record of what was delivered — and the baseline tracks the last approved
  state. The one documented deviation of the old baseline (the German/English schema break
  between `chapters.json` and `deutung.json` in the July run) does not exist in the replay
  and was dropped with it; the `known_deviations` mechanism itself is unchanged.

### Added

- Every run writes a `manifest.json`: schema version, normpare version, timestamp, both
  input files with their SHA-256 and every effective parameter. Without it an output
  directory cannot say what produced it.
- The interpretation stage takes a provider (`DeutungProvider`). `LiveProvider` is the
  existing path; `FixtureProvider` serves frozen answers from a JSON file, which makes the
  stage testable without a network call.
- A fifth evidence field, `evidence_unique`: the quote occurs verbatim in the addressed
  change record and in no other record of the same chapter. It is reported, not enforced.
- A synthetic corpus (`tests/fixtures/synthetic/`) exercising renumbering, moves, merges,
  additions, removals and four evidence cases. It contains no text from any real standard,
  so it ships with the package and runs in CI; `tools/build_synthetic.py` turns it into the
  DOCX pair the pipeline reads.

### Fixed

- A passage that survived into a merged successor paragraph is no longer reported as
  removed. Where two old paragraphs become one new one and the first is already paired,
  the merge pass finds no free window and the second was reported as dropped — a claim
  that a duty had fallen away while it stood verbatim in the new edition. A leftover old
  paragraph whose text is *contained* in an already paired new paragraph now joins that
  link (`kind: "merged"`, both sources in `old_ids`). Structural containment, not a
  lowered similarity threshold. Measured on the 4110 pair: 17 of 405 removal reports
  disappear, and the audit finds no false removal left.
- `manifest.json` no longer fails on a run without source files (a replay starts from the
  frozen `norm_doc.json`); a path that is not a readable file is recorded as absent.

### Added

- `tools/removed_audit.py` checks every reported removal against the full text of the new
  edition and classifies it: a genuine removal, a false positive in the mapped counterpart
  chapter (paragraph alignment), or a move into another chapter (chapter alignment). For
  false positives it also names the structural constellation behind them.

### Changed

- `evidence_ok` is being reframed. A literature review (2026-08) established that the
  current check measures **quote fidelity** — whether the cited wording actually occurs in
  the source paragraph — and not whether the cited span *supports* the interpretation, nor
  whether the interpretation stays within what the span licenses. Three separate metrics
  will replace the single figure: citation completeness, grounding coverage and attribution
  precision. The reported "99.88 % evidence" refers to quote fidelity and will be labelled
  as such.

### Planned

Agreed but not yet implemented. Rationale, sources and sequencing are recorded in the
internal decision log (`notizen/Entscheidungen_Literaturreview.md`, decisions ENT-01…17)
and the change strategy (`notizen/Aenderungsstrategie.md`).

- Four-axis change taxonomy replacing the single change label: structural operation,
  semantic status, normative direction, affected normative component.
- An explicit `undetermined` interpretation status with reason codes, alongside the
  existing review queue.
- Correspondence graph supporting 1:n, n:1 and n:m relations, with per-signal scores and
  alternative candidates retained on every edge.
- Score margin (best over second-best candidate) as the confidence measure, replacing raw
  similarity.
- Support and no-overflow verification of interpretations against cited spans, using a
  verifier independent from the interpreting model.
- Quote-first span resolution with recorded matching method and edit distance.
- Extended German cue inventory covering modal infinitives, lexical obligation phrases and
  indicative constructions, which the current modality detection does not capture.
- Work / edition / manifestation identity model for units across editions.
- Evaluation protocol measuring alignment and classification separately, with per-axis
  inter-annotator agreement on a gold subset.

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
