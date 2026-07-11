# normpare

**Find out what actually changed between two editions of a technical standard.**

When a standard is revised, the publisher rarely tells you precisely what changed. You get a
new PDF of several hundred pages and are left to compare it against the old one by hand.
`normpare` does that comparison for you: it reads both editions, matches them chapter by
chapter and paragraph by paragraph, and reports every change — new requirements, deleted
ones, changed limit values, reworded passages, moved sections, edited tables.

The comparison itself is **deterministic**: same inputs, same output, every time, with no AI
involved. On top of that you can optionally switch on an **AI interpretation** that explains
each change in plain language and separates real substantive changes from mere editorial
rewording. Every AI statement carries a verbatim quote from the source, and normpare checks
that the quote really exists — so you can always trace a claim back to the text.

## What you get

- An annotated **HTML** version of the new edition: changes colour-coded in place, with a
  summary per chapter, cell-level table diffs and embedded figures.
- Two **Word synopses** — a deterministic, tabular old ↔ new comparison, and (if AI is on) a
  final, readable one that bundles related changes into a single entry per topic.
- A **PowerPoint** draft of the key changes, machine-readable **JSON** for every stage,
  **tables as CSV**, and figures as image assets.

## Installation

Requires **Python 3.13**.

```bash
pip install normpare                      # deterministic comparison
pip install "normpare[anthropic]"         # + AI interpretation via Anthropic
pip install "normpare[embeddings]"        # + optional embedding-based alignment
```

## Usage

Two files in, one directory out:

```bash
normpare compare --old standard_2019.pdf --new standard_2026.docx --out result/
```

PDF and DOCX are both supported, and mixing them is fine. Without an API key the run is
purely deterministic; the AI prompts are exported so you can paste them into a chat instead.

From Python:

```python
import normpare

result = normpare.compare(
    old="standard_2019.pdf",
    new="standard_2026.docx",
    out_dir="result/",
    use_llm=False,          # True + an API key => AI interpretation
    language="de",          # language the standard is written in
)
print(result.html, result.synopse_det, result.synopse_final)
```

Useful flags: `--no-llm` (deterministic only), `--batch` (send the interpretation as one
Anthropic batch, about half the price), `--model` / `--provider` (choose the AI backend —
Anthropic, OpenAI, a local Ollama server, and others).

## Documentation

Full documentation, including how the pipeline works and how to pick an AI model:
<https://ce1ectric.github.io/normpare/>

## License

[MIT](LICENSE) © 2026 Christian Ehlert
