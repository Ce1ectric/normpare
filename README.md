# normpare

**Find out what actually changed between two editions of a technical standard.**

When a standard is revised, the publisher rarely tells you precisely what changed. You get a
new PDF of several hundred pages and are left to compare it against the old one by hand.
`normpare` does that comparison for you: it reads both editions, matches them chapter by
chapter and paragraph by paragraph, and reports every change — new requirements, deleted
ones, changed limit values, reworded passages, moved sections, edited tables.

The comparison itself is **deterministic**: same inputs, same output, every time, with no AI
involved. On top of that you can optionally switch on an **AI interpretation** that explains
each change in plain language. The pipeline records what happened structurally; the AI adds
what happened to the statement, whether the requirement got stricter or looser, and which
parts of the standard are touched. Every interpretation of a change, a table or a figure
carries a verbatim quote from the source, and normpare checks that the quote really exists;
values the AI reports for a table are checked against the table's cells. Failed quotes,
failed value checks and contradictions between the interpretation and the facts are
collected in a review queue — marked, never silently corrected.

## What you get

- An annotated **HTML** version of the new edition: changes colour-coded in place, with a
  summary per chapter, cell-level table diffs and, for DOCX sources, embedded figures.
- Two **Word synopses** — a deterministic, tabular old ↔ new comparison, and (if AI is on) a
  readable one with one entry per chapter: an overview, then every interpreted change.
- A **PowerPoint** draft of the key changes, machine-readable **JSON** for every stage,
  **tables as CSV**, and figures as image files when the source is a DOCX.
- **Working lists**: every interpreted change as a CSV row with its axes and its section in
  both editions, the changed values with their section and class, the same material grouped
  by affected component, and review lists of what needs a human look.

## Installation

Requires **Python 3.13**.

```bash
pip install normpare                      # deterministic comparison, OpenAI-compatible AI
pip install "normpare[anthropic]"         # + AI interpretation via Anthropic
pip install "normpare[embeddings]"        # + optional embedding-based alignment
```

## Usage

Two files in, one directory out:

```bash
# deterministic only
normpare compare --old standard_2019.pdf --new standard_2026.docx --out result/ --no-llm

# with AI interpretation via Anthropic (needs normpare[anthropic];
# key in ANTHROPIC_API_KEY or a file .api_key)
normpare compare --old standard_2019.pdf --new standard_2026.docx --out result/

# with any OpenAI-compatible endpoint, e.g. DeepSeek (key in LLM_API_KEY or .api_key)
normpare compare --old standard_2019.pdf --new standard_2026.docx --out result/ \
    --provider openai_compatible --base-url https://api.deepseek.com --model deepseek-v4-flash
```

PDF and DOCX are both supported, and mixing them is fine. Without an API key the run is
purely deterministic; the AI prompts are exported so you can paste them into a chat instead.
Answers are cached, so an interrupted run resumes where it stopped.

From Python:

```python
import normpare

result = normpare.compare(
    old="standard_2019.pdf",
    new="standard_2026.docx",
    out_dir="result/",
    use_llm=False,          # True + an API key => AI interpretation
    language="de",          # language of the AI's free-text output
)
print(result.html, result.synopse_det)
```

Other useful flags: `--batch` (send the interpretation as one Anthropic batch, about half the
price) and `--provider` (Anthropic, OpenAI, Azure, Google, Mistral, Groq, Together,
OpenRouter, a local Ollama server, or any OpenAI-compatible endpoint). With any provider
other than `anthropic`, name the model with `--model`.

## Status

Version 0.2.0 has been run end to end over three complete standard revisions with 1,100 to
2,400 changes each: every change sent to the model was answered, and 98 – 99 % of the AI's
quotes were verified in the source. In a manual spot check of fifteen central changes, made
on the runs just before the release, every value change found by the deterministic diff was
right; the errors were an AI statement about a table and a false alarm of the modality
detection. The interpretation is a reading aid with verified quotes, not a legal
assessment.

## Documentation

Full documentation — how to read the results, how the pipeline works, how to pick an AI
model: <https://ce1ectric.github.io/normpare/>

Changes between versions:
[CHANGELOG.md](https://github.com/Ce1ectric/normpare/blob/main/CHANGELOG.md)

## License

[MIT](https://github.com/Ce1ectric/normpare/blob/main/LICENSE) © 2026 Christian Ehlert
