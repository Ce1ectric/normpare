# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **DeepSeek as an interpretation provider — thinking off, and counted when it happened
  anyway.** `deepseek-v4-flash` needs no provider of its own: `--provider
  openai_compatible --base-url https://api.deepseek.com --model deepseek-v4-flash` covers
  it, and the token budget (`MAX_ANSWER_TOKENS = 48000`) fits its limits.
  - The OpenAI-compatible request body now carries `"thinking": {"type": "disabled"}`, the
    same value the Anthropic path has sent since AP-18. Without it every request against
    DeepSeek would run in thinking mode (default effort `high`): the chain of thought is
    billed as output, and `temperature: 0` — which the pipeline sets for reproducibility —
    is ignored while thinking is on. No per-provider special case; providers that do not
    know the field ignore unknown fields.
  - Should an answer come back with a `reasoning_content` field anyway, the provider
    thought and billed it. That is counted per request and reported in the summary of the
    interpretation stage and in `pipeline_feedback` (`field: "reasoning"`) — both only
    when the count is non-zero. The field is never parsed and never cached: it holds what
    the model discarded, not what it answered.
  - `docs/providers.md` documents DeepSeek as the worked example for `openai_compatible`.
    `--batch` has no effect there: the message-batch API is Anthropic's, and every other
    provider falls back to sequential calls.

- **Interpretation without an API: the prompts go out, the answers come back in.** The
  answer cache is keyed on `model + system prompt + user prompt` and knows nothing about
  the provider, so a JSON file under the right key *is* an interpretation — the pipeline
  will not ask about that chapter again. Two tools make that usable when no API budget is
  left.
  - `tools/pending_prompts.py --dir <run> --work <dir>` rebuilds every request a run over
    that directory would send — with the production code, split blocks included — and
    exports only those with no cache file: one raw prompt per file, the `system_prompt.txt`
    that carries the interpretation rules, and a `manifest.json` with key, chapter, block
    and the change indices of each. Measured on the two production runs: **52 open prompts
    for 4110** (940 528 characters, median 15 779, max 47 290) and **47 for 4120** (831 618,
    median 14 814, max 45 051).
  - **The key recipe is checked before anything is written.** Model and system prompt enter
    every key alike, so if either is wrong no cached answer can be found at all — then the
    tool aborts naming both, instead of producing answers the pipeline would never pick up.
    A prompt whose *body* drifted since its answer was written is a different fact: it is
    reported and asked again, which is what a run would do.
  - `tools/apply_answer.py --work <dir> --answer NNNN --file <json>` (or `--batch <dir>`)
    checks an answer and stores it atomically — **checking, not repairing**: parse (strict,
    then lenient, and `repaired` is reported), expected keys, every `change_index` within
    the block this prompt showed, pipeline-owned fields dropped and counted. A quote that
    is not in the prompt is a **warning**, not a rejection; the evidence guard of the
    pipeline stays the authority.
  - `--status` reports what is answered by now and names what is missing, so an
    interpretation spread over several nights resumes without bookkeeping: the cache is
    the progress.
  - `run_deutung`'s prompt building moved into `chapter_jobs()`, so tool and pipeline ask
    the same question from the same code path, character for character.
- **The tables reach the model whole, and every cut says so.** The cell text of a table
  was capped at 1100 characters and the whole table/figure block at 6500 — both without a
  word in the prompt. That hit 115 of 233 tables in the 4110 run and 89 of 171 in 4120:
  half of the tables, and tables are where the limit values live.
  - `TABLE_CHARS = 8000` and `ASSET_BLOCK_CHARS = 32000` are named constants carrying the
    measurement that justifies them: the largest rendered table of either corpus is 6752
    characters and the largest block 29 694, so **no table and no block of either corpus
    is clipped any more**. Cost, measured on both finished runs: +102 158 characters of
    prompt for 4110 (+4.1 %) and +105 191 for 4120 (+5.1 %); 20 of 163 and 17 of 145
    cached chapters get a new prompt, all others still hit the cache.
  - A cap that does bite now **says so**: `… (gekürzt, +n Zeichen)` for a table,
    `… (Block gekürzt, +n Zeichen)` for a block — the shape the row cap has always used.
  - Every rendered table and block is **counted**, clipped or not, so the coverage report
    can say `Tabellen gekürzt: 0 von 233` and mean a measured zero. The numbers appear in
    the console summary, in `deutung.json` under `coverage` and in `pipeline_feedback`,
    with chapter and table id for each cut. The row cap (`max_rows = 40`) is unchanged.
- **Every change is asked about, not only the most important forty.** A chapter prompt
  was capped at 40 changes and the rest was dropped without a word: 714 of 2506 changes
  in the 4110 run (28.5 %) and 552 of 2065 in 4120 (26.7 %) never reached the model, in
  the professionally most important chapters — the proof procedures, the definitions,
  the connection conditions. Whoever built training material from the result held it for
  complete.
  - A chapter with more changes than fit into one request is now **split into blocks** of
    `MAX_CHANGES_PER_REQUEST` (40). The priority order is untouched, so block 1 holds the
    same 40 changes as before and renders byte-identical to the previous prompt: a repeat
    run pays for the new blocks only (proven on both production runs, 163 and 145 cached
    answers hit again, 0 orphans). Tables and figures travel with block 1 alone.
  - The answers are merged by `change_index`; if two blocks claim the same one, the
    **earlier** block wins and the collision is counted.
  - Cost, measured on the finished runs: 24 extra requests for 4110 and 20 for 4120,
    370 kB and 270 kB of extra prompt.
- A second, **lenient parse** of an answer that the strict parser rejects
  (`json.loads(..., strict=False)`): a raw control character inside a string made three
  complete answers unusable across the two runs. Such an answer counts as `repaired`,
  never as `ok` — how often the model delivers invalid JSON has to stay countable. There
  is no third attempt: cutting, bracket-balancing or regex patching would invent content.
- **The coverage is reported.** `Änderungen: 2506, davon gedeutet 2506 (100,0 %)` in the
  console summary, with the split chapters, the repaired answers and every chapter that
  still has uninterpreted changes named. The same numbers machine-readable in
  `pipeline_feedback` (phase `deutung`, field `coverage`) and in `deutung.json` under
  `coverage`. Below 100 % the two AP-17 deliverables say so as well: a head note in
  `Aenderungen_nach_Komponente_<run>.md` and a companion file
  `Aenderungen_<run>_Abdeckung.txt` beside the change CSV, which cannot carry prose
  itself.

- A reason for every chapter that has no interpretation, and a summary at the end of the
  stage that is printed whether anything failed or not. Two production runs lost 8 of 171
  and 10 of 155 chapters — the ones with the most changes, which is to say the ones that
  matter — and said so for 5 of them; the rest fell back to the extractive summary in
  silence and the run still ended with a list of files. The defect was not the token
  limit but the silence.
  - Four outcomes per chapter: `ok`, `truncated`, `api_error:<type>` and `unparsable`
    (plus `export` for a run without a key and `no_answer` for a provider that keeps no
    outcomes). Truncation is read from the answer's `stop_reason`, not guessed from its
    length, and an `api_error` carries the type the API itself reported —
    `overloaded_error` is worth a repeat run, `invalid_request_error` is not, and "error"
    cannot tell them apart. Both paths are covered, the message batch and the sequential
    one.
  - `Deutung: 171 Kapitel, 163 gedeutet, 8 ohne Deutung` on the console, one line per
    reason with the chapters named, and a pointer to the prompts and raw answers. The
    same numbers go into `pipeline_feedback` under the phase `deutung` — one entry per
    lost chapter plus a total — so a later run can compare them.
  - A `.FAILED.txt` now keeps the outcome, the system prompt, the user prompt **and** the
    raw answer. Until now it held the answer alone.

- Two new deliverables that put the four axes in front of a reader, and a marker in the
  two existing ones. Until now the axes existed only in `deutung.json`: the 238
  interpretations that touch a `proof_obligation` in the 4110 run were there and visible
  to nobody who does not read JSON.
  - `Aenderungen_<run>.csv` — one row per interpretation, 17 columns in a fixed order
    (`section_id`, `mapping_id`, `chapter_title`, `change_index`, `change_kind`, the five
    axis fields, `semantic_label`, `obligation`, `change`, `impact`, `evidence`,
    `evidence_ok`, `confidence`), so the result can be filtered and pivoted in a
    spreadsheet. Axis D is one column, its values separated by `|` in the delivered order
    ("most important first"). Written as **UTF-8 with BOM** and separated by
    **semicolons** — a deliberate deviation from `csv_export.py`, which exports document
    tables for tools and keeps its plain comma form; this file is opened by a double
    click in a German Excel.
  - `Aenderungen_nach_Komponente_<run>.md` — the same material grouped by **affected
    component** instead of by chapter: an overview table of component against normative
    direction, then one section per vocabulary value in the order of
    `AFFECTED_COMPONENTS` (not by frequency, so two runs stay comparable), then `other:`
    with its free labels and a section for interpretations that name no component. An
    interpretation naming several components appears in each of their sections; the axis
    is multi-valued and hiding that would misrepresent it. On the 4110 run 1595
    interpretations produce 2352 entries, 59 % of them appearing exactly once.
  - A short marker `[narrowed · relaxed · scope, limit_value]` behind the existing label
    in the final synopsis and in the annotated HTML, which also carries the abstention
    reason (`indeterminate_reason`) as a tooltip. The axis values are shown in their
    neutral schema wording: 27 of 36 have no German rendering yet, and a half-translated
    marker would read as two vocabularies at once. `semantic_label` and `obligation` stay
    where they are, and the slide deck is untouched — slides are for the overview.
  - A run written before the axes existed keeps working: empty cells in the CSV, no
    marker, and a component view that says why it is empty. Shown on a real pre-axes run
    (`out/60909_deutung`, 1048 interpretations, not one axis field) through the whole
    report stage, not only in a test.
- Four orthogonal axes on every interpretation, next to the two fields they will
  eventually replace (ENT-01): `structural_operation` (A), `semantic_status` (B),
  `normative_direction` (C) and `affected_components` (D). The flat `semantic_label`
  mixes all three of them, and it is measurable: over the 1749 interpretations of the
  4110 run, `restricted` splits 14 / 15 / 13 over `tightened` / `relaxed` / `unchanged`
  — "the *scope* was restricted" (a relaxation for whoever is bound) and "the
  *requirement* was restricted" (a tightening) are the same value today. Another 84
  interpretations claim a duty appeared or fell away and report `unchanged` with it.
  Together 126 of 1749 = 7.2 % contradict themselves.
  Axis A is **pipeline-owned**: derived from the change record's `kind`, never asked of
  the model, and a supplied value is discarded and counted like any other pipeline-owned
  field. Axes B, C and D have closed vocabularies; `narrowed` replaces `restricted` and
  is a statement about scope only, `not_applicable` gives non-normative text a direction
  of its own instead of forcing it into `unchanged`. Axis D is multi-valued, most
  important first, and accepts `other:<short label>` where nothing fits — every use is
  reported with its chapter, which is how the proposed vocabulary gets corrected.
  A value outside its vocabulary, and a missing field, are **discarded and counted, never
  corrected**: reading `restricted` as `narrowed` would measure the correction instead of
  the model. `semantic_label` and `obligation` keep running unchanged, so one run
  collects both readings on the same chapters.
- Abstention on axes B and C (ENT-02): `indeterminate` with a reason code from
  `no_evidence | ambiguous_scope | conflicting_signals | outside_text`. An abstention
  without a valid code is not an abstention — the axis is emptied and counted. Axis D
  has no abstention: which component is affected is a question about the text, not a
  judgement that can be left open.
- `tools/axis_report.py` measures the result over a finished run, offline and without an
  LLM: the cross table B × C, the share of self-contradictory combinations under both
  schemas (the rules are constants of the module, and the old ones reproduce the 7.2 % of
  the 4110 run exactly), the spread of `narrowed` over axis C, the migration table
  `semantic_label` × `semantic_status`, axis D with every `other:` value, and the
  abstention codes including their overlap with the old `restricted`.
- Field ownership in the interpretation stage: every field the pipeline knows itself is
  now set by the pipeline, and a value the model supplies for such a field is **discarded
  and counted** instead of silently overwritten. The counts go into `pipeline_feedback`
  under the phase `deutung`, one entry per chapter and field. Declared in
  `PIPELINE_OWNED_CHAPTER`, `PIPELINE_OWNED_INTERPRETATION` and `PIPELINE_OWNED_ASSET`:
  the key of an answer (`section_id`, `mapping_id`), the stage's own bookkeeping
  (`_source`, `_changes_total`, `_changes_interpreted`) and every computed evidence
  metric. Over the 4110 answers the model supplied exactly one of them, `section_id`, in
  all 170 chapters — ten of which named a chapter that does not exist.
- `change_index_best` and `change_index_disputed` on every interpretation: the quote is
  scored against *every* change record of its chapter, not only against the one the model
  chose. `change_index_best` names the strongest candidate, `change_index_disputed` says
  it is strictly better than the chosen record. Which change an interpretation is about
  stays the model's decision — it can be right for a reason text similarity cannot see —
  so nothing is corrected: a disputed interpretation enters the review queue and a human
  decides. A tie is no contradiction. Over the 4110 run 91 of 1387 interpretations
  (6.6 %) are disputed, all of them among the 200 that were already not uniquely
  localizable.
- Every review-queue entry names its `review_reasons`: `contradiction_flag`,
  `evidence_ok`, `change_index_disputed`. The existing triggers keep their meaning
  exactly; the cross-check is an additional one, distinguishable from the evidence guard.
- `tools/evidence_report.py` reports the cross-check as well (`change_index_disputed`,
  and the two consistency checks against `evidence_unique`), so it can be measured over a
  finished run without an LLM.
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

- **The modality of a change is read off the sentences that changed, not off the whole
  paragraph.** The paragraph maximum answered a different question than the change asks: a
  paragraph keeping one "muss" reported "muss" on both sides however its other sentences
  were rewritten, so `sollte → muss` next to it vanished — 8 % of the changes of one corpus
  carry sentences of differing modality in one paragraph, which is the set in which the
  aggregation had to lose something. `sentence_modality()` splits both versions with the
  existing sentence splitter, pairs the sentences (a sentence diff for what is literally
  unchanged, an optimal assignment inside every changed block, threshold
  `SENTENCE_PAIR_MIN = 0.60`) and reports the **strongest shift among the pairs**: the
  largest distance on `RANK`, ties going to the first pair in the new text. Without a moved
  pair the two paragraph maxima stay in place, exactly as before. The new, additive
  `modality.sentence_shifts` lists every moved pair and every sentence without a partner
  (`hinzugefuegt`/`entfallen`), each with both labels and a shortened sentence, so what the
  summary summarises stays readable.
- **A swap inside the same deontic class is no shift.** `RANK` puts `darf` above `kann`, so
  every one of those swaps read as a tightening — and the new edition of one corpus makes
  that swap throughout as a wording alignment. `shift()` now returns `unveraendert` when
  both labels carry the same deontic class (`LABEL_CLASS`, derived from `DEONTIC_CLASS`
  with the two permission classes merged); `RANK` and `DEONTIC_CLASS` themselves are
  untouched, because other places use them for the ordering they describe. Measured over
  three corpora, the rule drops **37, 14 and 4** sentence pairs, every single one of them
  `kann → darf` — the opposite direction does not occur. Together with the sentence scope
  the reported shifts go from 74 to 27, from 41 to 16 and from 18 to 9, and **9, 6 and 1**
  shifts that the paragraph maximum had masked appear for the first time. Nothing but
  `changes[].modality` and `comparison.modality_shifts` moves.
- **A `cosmetic` verdict no longer hides a substantive change.** A change whose visible
  operations are all filtered away is called cosmetic — right for hyphenation and
  typography, wrong for "mindestens 5 %" → "mindestens 1 %", whose single deleted digit is
  not substantive on its own. Three signals now veto the verdict: a changed parameter value
  (`kennwerte.changed`), a changed set of qualifier words (`QUALIFIER_WORDS`, a closed list
  of ten words that turn a duty into a reservation or back), or a changed reference
  (`refs_diff`, all four lists). The change falls back to `similar` and is not marked
  semantically equal. Counting digits is deliberately **not** implemented: "some digit
  differs" would hit 53 of 75 cosmetic changes in one corpus and 55 of 82 in the other —
  standard numbers, years and cross-references. Measured over three corpora, **18 of 75,
  14 of 82 and 1 of 35** verdicts fall, most of them over a changed reference.
- **A paragraph split in two is one change, not two.** The new edition splits an old
  paragraph; the aligner paired the first half and reported the second one as `new`
  although it stood verbatim in the old text — the same sentence counted once as a deletion
  and once as an addition. A post-pass after all existing passes takes a leftover new
  paragraph of at least 40 comparison-key characters whose text is contained in an already
  paired old paragraph, widens that pairing to 1:2 and reports it as `split`; containment,
  never a lowered threshold. One old paragraph absorbs at most one extra half — further
  hits stay `new` and are counted, which keeps a collective paragraph such as a
  bibliography from swallowing every new entry. Effect over three corpora: `new` drops from
  1269 to 1220, from 1021 to 965 and from 1219 to 1211. Nothing but `changes[].kind`,
  `changes[].semantic_equal` and the pairing itself moves.
- **A table that changed chapters is a move, not a deletion plus an addition.** `tables_diff`
  compares the tables of *one* chapter mapping, so a table standing in 11.4.21 in the old
  edition and in 11.4.24 in the new one was never put next to its counterpart — two
  identical certificate tables (similarity 1.000 and 0.995) were counted once as a deletion
  and once as an addition. `cross_chapter_tables()` now runs after all chapters are paired,
  puts every leftover `removed` against every leftover `new` of the whole document, assigns
  them optimally (`linear_sum_assignment`) and reports a hit above `CROSS_CHAPTER_MIN = 0.80`
  as `moved_away` at the old place (naming the new table and `moved_to_chapter`) and
  `moved_in` at the new one (naming the old table and `moved_from_chapter`), with the same
  row comparison a paired table gets. Additive: the three existing `kind` values keep their
  meaning, and whoever reads `removed` gets fewer messages. The threshold is measured, not
  set: on all three corpora 0.80 falls into the widest gap between two neighbouring assigned
  pairs (4110 0.769 → 0.861, 4120 0.629 → 0.852, 60909 0.361 → 0.836). Effect: 9 / 5 / 1
  moves, `removed` 55 → 40, 34 → 23, 6 → 4 and `new` 81 → 66, 63 → 52, 7 → 5. The annotated
  HTML shows a moved table where it now stands and names the chapter it came from; the
  interpretation prompt carries the cells of both ends as it did before.

- **The head of a table carries its identity, the body does not.** The content window goes
  back from ten rows to three (`CONTENT_ROWS`), and the 0.9 penalty stays gone. In a form
  the first rows are the header both editions share and everything below is filled in, so a
  wider window compares entries instead of tables (4110 B.11.2 0.850 → 0.407, E.13
  0.954 → 0.287). Measured over all three corpora before the change: the narrow window loses
  none of the repairs of the previous release — 10.3.4 and 10.3.5 pair identically at both
  widths, only with higher confidence (0.62 → 0.985), which shows that repair came from
  comparing the caption without its number — and wins back six pairings at 4110, six at 4120
  and one at 60909.

- **Tables are paired by what they say, not by the number in front of it.** The pairing
  was greedy over the new tables in document order, compared the *whole* caption
  including its number, and looked at the cells only when a caption was missing. In
  4110/10.3.4 that paired the old table 11 with the new table 16, reported the new table
  17 as an addition and the old table 10 as a deletion — a set of value changes that does
  not exist, while the one real change of the chapter appeared in no reported row. Three
  things changed together:
  - `caption_text()` accepts a string as a caption only when it looks like one (keyword,
    number, separator, at least three characters of text) and returns the descriptive
    rest **without** the number. `"Tabelle 10 empfohlen."` is the end of the preceding
    sentence, so it now counts as a missing caption; the raw string stays in the record,
    because the display still uses it. Measured over both corpora: 4 of 16 and 3 of 14
    caption fields on the PDF-read old side fail the check, none at all on the DOCX side.
  - `table_similarity()` takes `max(caption, content)` instead of falling back to a
    content comparison penalized by 0.9. The content window grew from 3 to 10 rows
    (`CONTENT_ROWS`), still capped at 400 characters after normalization.
  - `_assign_tables()` builds the similarity matrix over all old × new tables of a
    chapter and assigns them optimally (`linear_sum_assignment`, the same wrapper the
    paragraph aligner has used all along); the threshold `TABLE_MATCH_MIN = 0.55` applies
    **after** the assignment, so a weak pair falls apart into `removed` + `new`.

  Effect on the two production corpora: 4110 goes from 19 to 17 pairs, 4120 from 15 to
  13, and the rows reported as changed drop from 236 to 152 and from 200 to 148 — those
  were the invented ones. 60909 is unchanged. `10.3.4` now reports `11 ↔ 17` and
  `10 ↔ 16`, `10.3.5` likewise. Nothing outside `tables_diff` moves: with every
  `tables_diff` key removed, `chapters.json` and `synopse.json` are byte-identical before
  and after on all three corpora. Tables that changed chapters between editions are still
  out of reach — the diff compares within one chapter mapping.
- The per-answer token budget is the named constant `MAX_ANSWER_TOKENS`, raised from
  16000 to **48000**. At 16000 the answer to chapter 10.2.2 of the 4110 run stopped
  inside a JSON string after 35 113 characters — 2.19 characters per token — with all 40
  interpretations delivered (a chapter prompt asks for at most 40 changes) and the figure
  block cut off. 48000 is about 2.8 times what the largest observed answer needed.
  Raising the limit costs nothing on a repeat run: the answer cache is keyed on model,
  system prompt and user prompt and **not** on `max_tokens`, which is now shown on both
  finished runs rather than assumed — every one of the 163 and 145 cached answers is hit
  again by the rebuilt prompts, and exactly the 8 and 10 lost chapters would be asked
  anew. Note that a model whose output limit is below 48000 will now reject the request;
  the run says so per chapter instead of failing quietly.
- Axis D has fifteen values instead of ten: `formula`, `note`, `heading`, `caption` and
  `example` join the vocabulary in front of `none`, the ten older ones keep their order.
  They are the five largest clusters of the free `other:` labels of the first two axis
  runs, which used the escape hatch in 13.9 % (4110) and 20.5 % (60909) of all
  interpretations — too much for a vocabulary meant to structure training material. The
  remaining ~30 % of genuinely subject-specific single cases are what `other:` stays for.
  Terminology and notation formed a sixth cluster and deliberately did **not** become a
  value: they overlap `definition` and would have built in the next ambiguity, so a fifth
  separation rule tells them apart instead (`definition` also for a changed designation,
  spelling or symbol notation; `formula` only when the equation itself changes). The
  description also directs recognisable preprocessing artefacts to `pipeline_feedback`
  rather than onto the axis. Together +573 characters per chapter call, so **the answer
  cache is out of reach again**.
- The contradiction rule `not_applicable_with_component` is dropped without replacement.
  It flagged non-normative text that touches a normative component and produced 567 of
  589 reported contradictions on 4110 and 712 of 715 on 60909 — every sampled case sound:
  the title of a referenced standard changes (`reference`) without any duty moving. Axis
  D says *what* a change is about, axis C whether a duty moves; the rule equated the two
  and so measured the very category error the axes remove. Without it the share of
  self-contradictory interpretations is **1.38 % (4110)** and **0.30 % (60909)**, against
  5.89 % and 6.24 % that the old flat schema produces on the same two runs. The two
  remaining rules are unchanged. `tools/axis_report.py` now lists every value of axis D
  in its comparison view, a frequency of zero included: whether a newly offered value is
  picked up at all is the measurement.
- The interpretation prompt describes the four new axes and says explicitly that
  `narrowed` is about scope and never about strictness — that confusion is what produced
  the defect — and it invites an honest `indeterminate` over a forced label. This makes a
  chapter prompt 862 characters longer (+11.95 % of the 7212-character mean of the 60909
  run) and the system prompt 447 characters longer. Since the answer cache is keyed by
  model, system prompt and user prompt, **every cached answer of an earlier run is out of
  reach**: a schema change means a full run, by construction.
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

- A PDF line is now read left to right instead of in drawing order. PyMuPDF returns the
  spans of a line in content-stream order, and in DIN EN 60909-0:2016 a subscript is drawn
  *before* its base glyph, so joining the spans as they arrive turned `i_p` into `"pi"`,
  `R_Gf` into `"Gf R"` and `I_k` into `"kI"`. The spans are now sorted by x before their
  text is joined; the sort is stable, so spans at the same x keep their order, and
  `size`, `bold`, `fonts` and `bbox` keep being formed over all spans. Measured over four
  PDF sources: 82 of 8738 lines change in the 2016 edition of 60909, 66 of 12197 in the
  2026 edition, 4 of 15396 in 4110 and 9 of 12185 in 4120, and every one of them is a
  correction. Section count, section ids, titles and the paragraph count per section are
  unchanged in all four.
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
