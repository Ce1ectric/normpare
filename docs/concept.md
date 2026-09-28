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
| **align** | align paragraphs within mapped chapters; detect moved, split and merged passages |
| **synopse** | the change diff — syntactic (exact character/token) and semantic (N3) levels, parameter-value changes, modality shifts, formula diffs, and tables paired over caption or cell content, across chapter boundaries where needed |
| **keywords** | assign a curated keyword taxonomy per chapter |
| **deutung** | per-chapter AI interpretation (summaries, per-change meaning and impact) on four axes, with an evidence guard — optional, skipped with `--no-llm` |
| **report** | build the deliverables (below) |

## What counts as a change

The paragraph alignment classifies every difference it finds. The `kind` of a change is
deterministic and is never asked of a model:

| `kind` | meaning |
|---|---|
| `new` / `removed` | a passage without a counterpart in the other edition |
| `similar` | a paired passage whose wording changed |
| `cosmetic` | a paired passage whose wording changed without changing the statement |
| `split` / `merged` | one passage became several, or several became one |
| `moved_in` / `moved_away` | the same passage at a different place — seen from the new and from the old side |

A move is only ever claimed **with evidence**: either the assignment finds the pair above
the similarity threshold — a stricter one for short passages, where a low bar pairs anything
— or the passage sits between two other moves into the same new chapter and travels with
them. A passage the aligner cannot place stays `removed` and, if its text is demonstrably
still somewhere in the new edition, appears in the review list described below. A third
route exists only on request: `--embed-fallback` adds an embedding-based rescue, described
under [Usage](usage.md#alignment-deterministic-default-and-optional-embedding-cascade).

A move is **one** event with **two** change records, and both are always written — also
when one side sits in a chapter that has no counterpart at all, where the passage would
otherwise be reported a second time as an addition or a deletion, so that one change is
counted twice. The pointer between the two records is evidence or it is not written: where
a move has no target record, the change names the target *chapter* (`moved_to_chapter`)
instead of the target paragraph, the same way a passage that travelled inside a block
does.

## The four axes

Where a single label would have to carry three statements at once, normpare asks four
independent questions about each change:

| Axis | Field | Vocabulary |
|---|---|---|
| **A** — the structural operation | `structural_operation` | `added`, `removed`, `modified`, `merged`, `split`, `moved`, `unchanged` |
| **B** — what happens to the statement | `semantic_status` | `equivalent`, `clarified`, `extended`, `narrowed`, `replaced`, `contradictory`, `indeterminate` |
| **C** — what it means for whoever is bound | `normative_direction` | `tightened`, `relaxed`, `unchanged`, `not_applicable`, `indeterminate` |
| **D** — which components are touched | `affected_components` | `proof_obligation`, `limit_value`, `procedure`, `deadline`, `responsibility`, `documentation`, `scope`, `definition`, `reference`, `formula`, `note`, `heading`, `caption`, `example`, `none`, or `other:<label>` |

Axis A is **derived from the change itself**, not asked of the model. Axis B is about scope
and wording, axis C about strictness — `narrowed` means "covers fewer cases", never
"stricter". A value outside its vocabulary is discarded and reported, never silently
corrected, and an axis may abstain with `indeterminate` plus an `indeterminate_reason`.

For a change **without** a counterpart axis B describes what happens to the body of
statements: added text is `extended`, text dropped without replacement is `narrowed`, and
`replaced` where a successor or predecessor is recognisable elsewhere. `equivalent` is
admissible only where the change record is no real change at all — a torn sentence, a
duplicate, a formatting artefact — and then the record has to be reported in
`pipeline_feedback`. Without that report `equivalent` is wrong here.

A move is one event with two change records, one in the old chapter and one in the new
one, and axis B describes the **text**, not the place: unchanged moved text is
`equivalent`, text reworded on the way takes the value that describes the rewording, and
`replaced` stays with a change whose counterpart is not connected by a move. Axis C says
what the *relocation* means for whoever is bound, and that is nothing: `unchanged` where
the moved text carries a duty, `not_applicable` where it does not, the same value on both
sides, and `tightened`/`relaxed` only where the text was changed on the way.

Both rules are answerable because the prompt shows the move from both sides. A change
record of a move carries only its own half — the old text where the passage left, the new
text where it arrived — so the request resolves the counterpart over the pointer and shows
it under the label of its side. Where the pointer names only the chapter, or names a
paragraph the document does not have, nothing is added: no placeholder and no guess.

## The axes against what the pipeline knows

The pipeline knows three things about a change without asking anybody: its structural
operation, which paragraph a move went to, and whether the changed sentences carry a modal
verb. Four checks hold the interpretation against them, and all four **mark without
correcting** — the reported value stays where it is:

- `axis_partner_disagreement` — the two records of one move are joined over the pointer the
  paragraph aligner left behind and compared on axes B and C. A difference is flagged on
  **both** sides, with the other side's value. A move whose pointer names only the chapter
  is not compared.
- `axis_b_contradicts_a` — `equivalent` for a change that has no counterpart at all. Where
  the chapter reports that very change in its `pipeline_feedback`, the record is counted
  **apart** instead: there the kind of the change is what is being disputed, at the place
  provided for it, and `equivalent` is the most honest answer the vocabulary offers. Both
  numbers are reported, at zero as well, and the flag stays on the record either way.
- `axis_c_contradicts_modality` — a change with a modal sentence called non-normative. The
  reverse case (informative text with a direction) is counted only, never flagged: the
  modality detection is not certain enough there.
- `successor_named` — a `narrowed` change whose own free text names the section the rule
  moved to.

Two guards of the deterministic stage sit next to them and must read zero: a `moved_away`
pointing at a paragraph nobody reports as `moved_in`, and a `moved_in` naming a source
nobody reports as `moved_away`. Either one means one event counted twice.

The counts reach `pipeline_feedback`; `tools/axis_consistency.py` recomputes all of them
over one or more finished runs, offline.

## The evidence guard

Every interpretation the model produces must include a short **verbatim quote** from the old
or the new text. After the model answers, normpare locates the quoted span in the answer
(a label in front of a correct quote does not count against it) and checks that it occurs
in the text of the change the interpretation addresses — its old and new side. It records
per entry:

- `evidence_ok` — the quote was found (its first 15 characters, or the whole quote; a
  quote under 15 characters passes only if it is the entire text of the change, which in
  practice means a pure addition or deletion),
- `evidence_strict` — the quote was found in full, the fragments between ellipsis marks
  checked separately,
- `evidence_match_chars` / `evidence_fragments` — how many characters matched, and how
  many ellipsis-separated fragments the quote has,
- `change_index_disputed` — another change of the same chapter fits the quote better. The
  case is flagged and never corrected.

A table or figure interpretation is checked against the whole chapter instead — captions,
cells and paragraphs — with a slightly different rule for quotes that render a table row.

An interpretation whose quote fails `evidence_ok`, whose change index is disputed, or that
the model itself flagged as contradictory lands in a **review queue** in `deutung.json`
with the reason attached — as do the two axis findings `axis_partner_disagreement` and
`successor_named` described above. `evidence_strict` and `evidence_unique` are reported,
not queued.

This turns model quality into something you can measure rather than trust: a strong model
verifies at well over 90 %, and the gap to a weak one is large.
See [LLM providers](providers.md#choosing-a-model-measure-do-not-guess).
[Working with the results](results.md#the-review-queue) explains each review reason.

## Values in tables

The deterministic value diff reads paragraph text. Values in table cells are extracted
too — every table in `norm_doc.json` carries `cell_values`, the numbers of each cell read
with the rules of a cell, where the unit stands in the column heading and a bare number
with a decimal place counts (a whole number without a unit does not: it would match row
numbers and footnote marks) — but no deterministic stage compares them.
`statistics.json → comparison → cell_values_unexamined` says how many there are, so that
an empty `kennwert_changes` is never read as "no value changed".

What an AI run says about a table is checked against those cells instead. A table
interpretation names its table (`table_ref`); the pipeline resolves that name to one table
of the chapter and writes the result as `table_id` — the model says which table it means,
the pipeline decides which table that is. Every value the entry claims in
`value_changes` — read with the same rules as a cell — is then looked up in the cells: the
old half of an arrow in the old edition, the new half in the new one, a value without an
arrow in both, and both halves in the one edition a table without counterpart has. The
entry records

- `values_checked` / `values_found` — how many values were recognised, and how many of
  them stand in the named table,
- `values_ok` — all of them do (also `true` when nothing could be checked, so read it
  together with `values_checked`),
- `values_elsewhere` — how many of the missing ones stand in another table of the same
  chapter.

A failed check puts the entry into the review queue as `values_ok`, or as the weaker
`values_in_other_table` when every missing number is accounted for in a neighbouring
table. An entry that names no table of its chapter cannot be checked; it is counted as
**unchecked** in `pipeline_feedback`, never as wrong. The check proves that a number is in
the table; it cannot prove that the number sits in the row or column the text assigns it
to.

## Coverage: what was asked, and what came back

The interpretation stage is the only part that can silently lose material, so it counts
separately what there was, what was asked and what came back, and prints all of it:

```
Änderungen: 1103, vorgelegt 1101, gedeutet 1101 (99,8 %)
  ohne Antwort: 0
```

- `n_changes_total` — every change of the comparison,
- `n_changes` — those a prompt actually asked about; the difference are chapters whose
  changes are all equal on the semantic level (N3) and are therefore not asked about,
- `n_interpreted` — those an answer came back for, counted **after** the answers are merged,
- `n_unanswered` — asked about, but no interpretation returned. When this is not zero, the
  affected chapters are named on the console, the component view carries the note inline,
  and the CSV gets a companion file `Aenderungen_<run>_Abdeckung.txt` beside it — a warning
  cannot go *into* a CSV without breaking it. A later complete run deletes that file again,
  so a stale warning cannot survive in the directory.

The percentage is `n_interpreted` over `n_changes_total`, so a complete run over a
standard with purely cosmetic chapters shows slightly less than 100 %.

A chapter with more changes than fit into one request is split into numbered part requests;
`n_split_chapters` and `n_extra_requests` record how often.

## Outputs (in the target directory)

**Documents**

- `annotiert_<run>.html` — the new version with colour-marked changes, chapter summaries,
  cell-diffed tables and embedded figures (figures from DOCX sources only; a PDF figure
  shows as a placeholder).
- `Synopse_deterministisch_<run>.docx` — tabular old ↔ new change synopsis (always).
- `Synopse_final_<run>.docx` — the AI-interpreted, human-readable synopsis: one entry per
  chapter, most training-relevant first, with an overview and every interpreted change
  (written whenever a `deutung.json` exists — see below).
- `Aenderungen_<run>.pptx` — a training slide draft of the key changes.

**Working lists**

- `Aenderungen_<run>.csv` — one row **per interpretation**, with its four axes, for a
  spreadsheet (UTF-8 with BOM, semicolon-separated, so a double click in a German Excel
  keeps its umlauts and columns). A change without an interpretation has no row here, which
  is what the companion `_Abdeckung.txt` warns about; without a `deutung.json` the file
  has its header only.
- `Aenderungen_nach_Komponente_<run>.md` — the same material grouped by affected component,
  for a reader.
- `Pruefliste_entfallen_<run>.md` — passages reported as removed whose text is demonstrably
  still in the new edition, with the reason: found outside the mapped sections, missed
  inside them, or a move the check could not confirm. An unbalanced chapter mapping is
  shown as an additional reason and used for sorting, but never puts a passage on the list
  by itself. The list **marks, it does not filter**: nothing is removed from the change
  stream.

**Machine-readable**

- `synopse.json`, `chapters.json`, `statistics.json`, `keywords.json`, `mapping.json`,
  `review_removed.json`, and `deutung.json` when the interpretation stage ran.
- Every change record in `synopse.json` carries **`section_old`** and **`section_new`**:
  the section its first paragraph sits in on each side, `null` where that side is empty.
  A chapter mapping is headed by one section, and depending on the standard between a
  quarter and four in ten changes stand in a subsection of it, so the block title and the
  place to look up are not the same thing. The sections come from the
  paragraph-to-section assignment of `norm_doc.json`, not from the shape of the paragraph
  id, and the reports show them next to a single change where they differ from the
  chapter head.
- `manifest.json` — inputs with their SHA-256, the stages that ran (with `use_llm`
  telling a deterministic run from an interpreted one), and every parameter of the run.
- `pipeline_feedback.md` — what the interpretation noticed: axis violations, truncated
  answers, unanswered changes, and what the model reported back about the extraction.
  Written by the interpretation stage.

The interpretation stage runs unless `--no-llm` is given, also without a key: it then
exports the prompts and still writes `deutung.json` (without interpretations) and
`pipeline_feedback.md`. The report stage uses whatever `deutung.json` is in the directory
— so a `--no-llm` run into the directory of an earlier AI run reports that run's
interpretation. Run a deterministic comparison into a fresh directory.
- `alt/` and `neu/` — the per-version `norm_doc.json` plus `assets/` (images, and each table
  as a CSV under `assets/tables/`).

**Caches**: `.vec_cache.npz` (encoded vectors of the embedding pass) is safe to delete and
cheap to rebuild. `llm_cache/` (one file per model and prompt, so a repeat run never pays
twice for a chapter already interpreted) is not cheap: deleting it re-asks — and re-bills
— every chapter, and the new answers will differ from the old ones. Keep it as long as the
run matters.

`llm_prompts/` is **not** a cache and should not be deleted unthinkingly. It holds the
exported prompts whenever the run cannot ask a model itself — no API key, no base URL, the
`anthropic` package missing, or provider `chat` — and in a live run it is where a truncated
or unreadable answer lands as `<tag>.FAILED.txt` (the tag is the chapter mapping id with
unsafe characters replaced, `.T2`, `.T3`, … for later parts), prompt and raw answer
together. That file is the only copy of what went wrong; the console points at
`llm_prompts/*.FAILED.txt`. An API error leaves no file — the console and
`pipeline_feedback` name the chapter and the error type.

## Known limits

- **Value changes are computed from paragraph text only.** A limit value that lives in a
  table cell does not appear in `statistics.json → comparison → kennwert_changes`; the
  number of such cell values stands beside it as `cell_values_unexamined`. With an AI run, table values
  are reported under `chapters[].tables[].value_changes` in `deutung.json` and checked
  against the cells ([Values in tables](#values-in-tables)) — a check of presence, not of
  position.
- **A value change is not always a changed number.** Every entry of `kennwerte.changed`
  carries **`value_class`**: `value_changed` when the normalized quantity moved,
  `operator_added` / `operator_removed` / `operator_changed` when only the operator in
  front of it did. The class is a label, not a verdict — `± 5 % → 5 %` drops an operator
  and still narrows the tolerance to one direction — so nothing is filtered by it; the
  reports only put the changed numbers first.
- **The `figures` count is not a count of figures.** For a PDF it counts placed image
  objects, so one drawing assembled from many pieces counts many times; for a DOCX it counts
  embedded drawings. The two are not comparable with each other. Figures are extracted as
  image files from DOCX only.
- **Modality is read from the sentences a change touches.** A requirement stated without a
  modal verb — a list item under a `muss` stem, for instance — can be missed, and a
  reported shift can be a false alarm.
- **A table reaches the model with at most 40 rows.** Longer tables are cut with a note in
  the prompt (`… (+n Zeilen)`); the console line `Tabellen gekürzt` counts only the
  character limit, not this row limit.
- **A document is read as delivered.** An international original appended to a national
  edition is ingested as part of its last chapter and produces spurious changes there.
  Cut such a PDF to the national pages before the run
  ([Input hygiene](results.md#input-hygiene)).
