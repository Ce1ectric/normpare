# Working with the results

A run writes a dozen files. This page says which one answers which question, where a
change is to be found in the standard, and what still needs a human look. The file names
carry the run name, written `<run>` below; the console and the deliverables are in German,
because the compared standards are.

## Reading order

1. **Is the run complete?** The console prints the coverage at the end of the
   interpretation, and `deutung.json → coverage` keeps it:

    ```
        Änderungen: 1103, vorgelegt 1101, gedeutet 1101 (99,8 %)
          ohne Antwort: 0
    ```

    `ohne Antwort` has to be `0`. Anything else means the model skipped changes, a request
    failed, or no model was asked at all (no key); the chapters are named, and a companion
    file `Aenderungen_<run>_Abdeckung.txt` stands beside the CSV. The percentage is taken
    over *all* changes, including chapters whose changes are purely cosmetic and are never
    sent to the model — which is why a complete run can show 99,8 %.
    [Concept](concept.md#coverage-what-was-asked-and-what-came-back) explains the counts.

2. **What changed, chapter by chapter?** `Synopse_final_<run>.docx` — one entry per
   chapter, most training-relevant first: an overview, then every interpreted change. It
   is the AI view; its fact base is `Synopse_deterministisch_<run>.docx`, which lists every
   change old ↔ new without interpretation.

3. **Which values changed?** `statistics.json → comparison → kennwert_changes`, the
   deterministic value diff of the paragraph text, in document order. The deterministic
   synopsis and the slides show the same list with the changed numbers first and the
   changes of the operator only (`15 MVA → mindestens 15 MVA`) after them — see
   [value classes](#value-classes) below.

4. **What changed in the tables?** The table interpretations in
   `deutung.json → chapters[].tables[]`; their values are checked against the cells of the
   table they name ([Concept](concept.md#values-in-tables)).

5. **What needs a second look?** `deutung.json → review_queue` and
   `Pruefliste_entfallen_<run>.md` — see [below](#the-review-queue).

`annotiert_<run>.html` is the new edition with every change marked in place; open it
when a change has to be read in context.

## Where a change stands

The interpretation is organised by **chapter mapping** — a block of the new edition
matched to a block of the old one. Its head is `section_id`; `mapping_id` names both
sides, the new sections before `<` and the old ones after it, for example
`3.1<3.1+3.1.1+3.1.10+~cf4ef82c` (long lists end in a digest). Depending on the standard,
between a quarter and four in ten changes do not stand in the head section but in a
subsection of it. Every change record therefore carries the section its paragraph
actually sits in:

| field | meaning |
|---|---|
| `section_old` | section of the old edition; empty for an addition and for the arriving side of a move |
| `section_new` | section of the new edition; empty for a deletion and for the departing side of a move |

The CSV has both as its last two columns and `kennwert_changes` has `section`. The
reports print the section next to a single change where it differs from the chapter
head. Look up `section_new` in the new edition, `section_old` in the old one.

## The CSV

`Aenderungen_<run>.csv` has one row per interpretation of a change — tables and figures
are not in it (UTF-8 with BOM, `;`-separated, opens directly in a German Excel):

`section_id; mapping_id; chapter_title; change_index; change_kind; structural_operation;
semantic_status; normative_direction; affected_components; indeterminate_reason;
semantic_label; obligation; change; impact; evidence; evidence_ok; confidence;
section_old; section_new`

Useful filters:

- `normative_direction = tightened` and `affected_components` contains `limit_value` or
  `proof_obligation` — the changes that most likely cost the reader something;
- `evidence_ok = false` — the quote of this interpretation was not found in the text of
  the change it addresses; treat the row as unverified;
- `semantic_status = equivalent` — editorial changes, safe to skim, **except** where
  `change_kind` is `new` or `removed`: a pure addition or deletion cannot be equivalent,
  and such a row is either an extraction artefact or a wrong reading.

`Aenderungen_nach_Komponente_<run>.md` carries the same material grouped by affected
component (axis D), with a count table at the top.

## Value classes

Each entry of `kennwert_changes` carries a `value_class`:

| `value_class` | example |
|---|---|
| `value_changed` | `mindestens 5 % → mindestens 1 %` |
| `operator_added` | `15 MVA → mindestens 15 MVA` |
| `operator_removed` | `± 5 % → 5 %` |
| `operator_changed` | `≤ 10 s → < 10 s` |

The class is a label, not a verdict: an operator change can be substantive (`± 5 % → 5 %`
narrows the tolerance to one direction). Nothing is filtered by it.

Values that sit in **table cells** are not in `kennwert_changes`. How many there are is
counted beside it in `comparison → cell_values_unexamined` (`old`, `new`, `total`) — an
empty `kennwert_changes` next to a few hundred unexamined cell values does not mean that
no value changed.

## The review queue

`deutung.json → review_queue` lists the interpretations that failed one of the checks
below, with the reasons attached. It **marks, it never corrects**: the interpretation stays
as the model gave it.

| reason | what it means | what to do |
|---|---|---|
| `contradiction_flag` | the model itself flagged a contradiction | often an extraction artefact (a torn sentence, a formatting remnant); check whether the chapter reports the same change in its `pipeline_feedback` |
| `evidence_ok` | the quote was not found (for a change: in its old and new text; for a table or figure: in the chapter) | treat the interpretation as unverified |
| `change_index_disputed` | another change of the chapter fits the quote better | compare with `change_index_best` |
| `axis_partner_disagreement` | the two records of one move are interpreted differently | decide which side is right; a move is one event |
| `successor_named` | a deletion whose own text names where the rule went | probably a move or a replacement, not a loss |
| `values_ok` | a table interpretation claims values that are not in the cells of its table | check the values against the table |
| `values_in_other_table` | the claimed values stand in another table of the same chapter | check whether the right table is meant |

Two further flags sit on the records but do not put them into the queue:
`axis_b_contradicts_a` (`equivalent` for a change without counterpart) and
`axis_c_contradicts_modality` (a change with a modal verb called `not_applicable`). Their
counts are in `deutung.json → consistency`, and the CSV filter above finds the first.

To list the queue:

```python
import json
d = json.load(open("OUT/deutung.json", encoding="utf-8"))
for e in d["review_queue"]:
    print(e["section_id"], e.get("change_index", e.get("asset")), e["review_reasons"])
```

`Pruefliste_entfallen_<run>.md` is the second list: passages reported as **removed**
whose text is demonstrably still in the new edition, each with the old text and where it
stands now.

`pipeline_feedback.md` collects what the interpretation noticed about the extraction —
hyphenation carried into the text, formatting remnants, lost formula symbols, suspected
pairing errors — each with the stage it points at (`ingest_pdf`, `alignment`, `diff`, …).
It is addressed to whoever maintains the pipeline, but it also explains many
`contradiction_flag` entries.

## What still needs a human look

A spot check of fifteen central points across three real standard revisions (made on
the runs just before this release) found every value change reported by the deterministic
diff right. One error was an AI statement about a table, one a false alarm of the
modality detection. Check these by hand:

- **AI descriptions of tables.** The value check catches a number that is not in the
  table. It does not catch a correct number assigned to the wrong row or column, a
  statement about a table's scope, or a whole number without a unit (those are not
  recognised as values). `values_ok` is also `true` when nothing could be checked
  (`values_checked = 0`). Read the table itself before relying on a table
  interpretation.
- **Modality shifts.** A shift such as `informativ → muss` is detected from the modal
  verbs of the changed sentences. It can be a false alarm; read the sentence.
- **Normative direction.** `tightened` / `relaxed` / `not_applicable` is the model's
  reading. Only one contradiction is flagged (`axis_c_contradicts_modality`: a change with
  a modal verb called `not_applicable`); a direction reported for informative text is
  merely counted, and any other value can still be wrong.
- **Operator-only value changes.** See [value classes](#value-classes).

The interpretation is a reading aid with verified quotes, not a legal assessment.

## Input hygiene

normpare reads a document as it is delivered. Two cases need attention before the run:

- **An appended original in another language.** Some national editions are delivered
  with the international original appended after the national text. That text is read as
  part of the last chapter (typically the bibliography), where it becomes hundreds of
  spurious changes. Signs: a disproportionate share of all changes in the last chapter,
  changes in the wrong language. Cut the PDF to the national pages first and keep the
  delivered file untouched:

    ```python
    import pymupdf  # installed with normpare

    src = pymupdf.open("delivered.pdf")
    out = pymupdf.open()
    out.insert_pdf(src, from_page=0, to_page=69)   # pages 1-70, zero-based and inclusive
    out.save("delivered_national_only.pdf")
    ```

- **A mix of PDF and DOCX** works, but the two are extracted differently. Figure counts,
  for instance, are not comparable between them
  ([Known limits](concept.md#known-limits)).

## Re-running

Every usable answer is cached in `llm_cache/`, keyed on the model, the system prompt and
the chapter prompt. Running the same comparison into the same directory again costs
nothing for chapters already interpreted, resumes an interrupted run where it stopped and
re-asks requests whose answer was truncated, unreadable or failed. An answer that parsed
but skipped changes is cached as it came back, and a re-run returns it unchanged.

Everything that enters the prompt enters the key: a different model, a changed
`--language`, or a **renamed input file** (its name appears in the system prompt) asks
every chapter again. A new output directory starts with an empty cache; copy `llm_cache/`
into it to reuse the answers.

A `--no-llm` run into a directory that already holds a `deutung.json` reuses that file for
the reports. Run a deterministic comparison into a fresh directory.
