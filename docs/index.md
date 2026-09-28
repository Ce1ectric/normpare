# normpare

**Find out what actually changed between two editions of a technical standard.**

When a standard is revised, the publisher rarely tells you precisely what changed. You get a
new PDF of several hundred pages and are left to compare it against the old one by hand.
normpare does that comparison for you: it reads both editions, matches them chapter by
chapter and paragraph by paragraph, and reports every change — new requirements, deleted
ones, changed limit values, reworded passages, moved sections, edited tables.

The comparison is **deterministic**: same inputs, same output, every time, no AI involved.
On top of that you can switch on an optional **AI interpretation** that explains each change
in plain language and separates substantive changes from mere editorial rewording. Every
interpretation of a change, a table or a figure carries a verbatim quote from the source,
and normpare verifies that the quote really exists — so each of them is traceable back to
the text. What the AI says about the values in a table is checked against the table's
cells as well.

## What you get

- an annotated **HTML** version of the new edition: changes colour-coded in place, a summary
  per chapter, cell-level table diffs and, for DOCX sources, embedded figures;
- two **Word synopses** — a deterministic tabular one, and (with AI) a readable one with one
  entry per chapter: an overview, then every interpreted change;
- a **PowerPoint** draft of the key changes, machine-readable **JSON** per stage, **tables as
  CSV**, and figures as image files when the source is a DOCX;
- the **changed values** with the section they stand in, changed numbers kept apart from
  changes of the operator only (`15 MVA → mindestens 15 MVA`);
- working lists to check the result against: every interpreted change as a **CSV row** with
  its four interpretation axes and its section in both editions, the same material
  **grouped by affected component**, a **review queue** of interpretations that failed a
  check, and a **review list** of passages reported as removed whose text is still in the
  new edition.

## Status

Version 0.2.0 has been run end to end over three complete standard revisions with 1,100 to
2,400 changes each: every change sent to the model was answered, 98 – 99 % of the quotes
were verified in the source. A manual spot check of fifteen central changes, made on the
runs just before the release, found every value change of the deterministic diff right;
the errors were an AI statement about a table and a false alarm of the modality
detection. [Working with the results](results.md) says what to check by hand.

## Start here

- [Usage](usage.md) — install it and run your first comparison
- [Working with the results](results.md) — what to read first, where a change stands, what
  to check by hand
- [Concept](concept.md) — how the pipeline works, stage by stage
- [LLM providers](providers.md) — choosing and configuring an AI backend
- [API reference](reference.md)
