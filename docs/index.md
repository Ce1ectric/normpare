# normpare

**Find out what actually changed between two editions of a technical standard.**

When a standard is revised, the publisher rarely tells you precisely what changed. You get a
new PDF of several hundred pages and are left to compare it against the old one by hand.
normpare does that comparison for you: it reads both editions, matches them chapter by
chapter and paragraph by paragraph, and reports every change — new requirements, deleted
ones, changed limit values, reworded passages, moved sections, edited tables.

The comparison is **deterministic**: same inputs, same output, every time, no AI involved.
On top of that you can switch on an optional **AI interpretation** that explains each change
in plain language and separates substantive changes from mere editorial rewording. Every AI
statement carries a verbatim quote from the source, and normpare verifies that the quote
really exists — so every claim is traceable back to the text.

## What you get

- an annotated **HTML** version of the new edition: changes colour-coded in place, a summary
  per chapter, cell-level table diffs, embedded figures;
- two **Word synopses** — a deterministic tabular one, and (with AI) a readable one that
  bundles related changes into a single entry per topic;
- a **PowerPoint** draft of the key changes, machine-readable **JSON** per stage, **tables as
  CSV**, and figures as image files when the source is a DOCX;
- working lists to check the result against: every interpreted change as a **CSV row** with
  its four interpretation axes, the same material **grouped by affected component**, and a
  **review list** of passages reported as removed whose text is still in the new edition.

## Start here

- [Usage](usage.md) — install it and run your first comparison
- [Concept](concept.md) — how the pipeline works, stage by stage
- [LLM providers](providers.md) — choosing and configuring an AI backend
- [API reference](reference.md)
