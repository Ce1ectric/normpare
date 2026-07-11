# Usage

## Installation

Requires **Python 3.13** and [Poetry](https://python-poetry.org/).

```bash
poetry install --with dev          # optional extras: --extras "anthropic embeddings"
poetry run pytest
```

## Command line

```bash
normpare compare --old OLD.(pdf|docx) --new NEW.(pdf|docx) --out TARGET/ \
    [--provider anthropic|openai|ollama|chat] [--model NAME] \
    [--language de] [--no-llm] [--embed-fallback] [--embed-model NAME] \
    [--config file.json|file.toml]

# equivalent:
python -m normpare compare --old … --new … --out …
```

- `--old` / `--new` — the two versions (PDF or DOCX; a mix is fine).
- `--out` — the target directory for **all** output.
- `--no-llm` — produce only the deterministic outputs (no API key needed).
- `--batch` — send the interpretation as one Anthropic message batch: **~50 % cheaper**,
  processed asynchronously (see below).
- `--embed-fallback` — optional embedding rescue pass (see below); off by default.
- `--config` — a JSON/TOML configuration file; it supplies the document metadata, and then
  `--old`/`--new` are optional.

## Cost: batching and caching

The interpretation stage is the only part that costs money. Two mechanisms keep it cheap:

Every answer is cached on disk, keyed by model + prompt, so a re-run never pays twice for a
chapter that is already interpreted. On top of that, `--batch` submits all uncached chapters
as a single **Anthropic message batch**, which Anthropic bills at **50 % of the normal rate**.
Batches are asynchronous — the run waits for the batch to finish instead of calling the API
chapter by chapter — so use it whenever latency does not matter:

```bash
normpare compare --old OLD --new NEW --out out/run --batch
```

Choose the model with `--model`. Note that a weaker model is a false economy here: the
interpretation is only trustworthy if its quoted evidence actually appears in the standard,
and that is exactly what small models fail at (the `evidence_ok` flag in `deutung.json` and
the review queue make this measurable).

## Alignment: deterministic default and optional embedding cascade

The paragraph alignment is deterministic by default: a TF-IDF backend (word + character
n-grams) plus numeric/heading anchors resolves the bulk of the pairs, and the result is
byte-reproducible across runs.

An **optional embedding rescue pass** (ADR-0001) can be switched on with `--embed-fallback`.
It runs *after* the deterministic pass, **within each chapter**, and only ever refines it:
paragraphs still left as `removed` / `new` in the same chapter are re-paired with a German
embedding model, so a paragraph that was reworded in place so heavily that TF-IDF missed it
becomes a single *changed* entry instead of a separate removal + addition. Cross-chapter
moves are deliberately left to the deterministic `moved` pass — a global embedding assignment
forces spurious pairings. The threshold is `tau_embed` (default `0.82`, tuned for precision:
on a real-world standard revision it recovered about a dozen genuine in-place rewordings). Encoded vectors are
cached (`.vec_cache.npz`, keyed by model + normalized text), so the pass is reproducible and
cheap on re-runs. It needs the `embeddings` extra:

```bash
poetry install --with dev --extras embeddings

# compare the two alignments to judge the difference:
normpare compare --old OLD --new NEW --out out/run_tfidf --no-llm
normpare compare --old OLD --new NEW --out out/run_hybrid --no-llm --embed-fallback
```

The rescued pairs are listed under `embed_rescue` in `mapping.json`.

Inspect an ingested document:

```bash
normpare inspect TARGET/neu/norm_doc.json
```

## Python API

```python
import normpare

result = normpare.compare(
    old="standard_2019.pdf",   # PDF or DOCX
    new="standard_2026.docx",  # PDF or DOCX
    out_dir="result/",
    provider="anthropic",      # or "openai" | "ollama" | "chat"
    language="de",             # language of the compared standard (configurable)
    use_llm=True,              # False -> deterministic outputs only
    embed_fallback=False,      # True -> optional embedding rescue pass (needs 'embeddings' extra)
)
print(result.html, result.synopse_det, result.synopse_final)
```

For fine control, use the pipeline directly:

```python
from normpare.config import Config
from normpare.pipeline import Pipeline

cfg = Config.for_compare("standard_2019.pdf", "standard_2026.docx", "result/")
Pipeline(cfg).run("all", use_llm=False)
```

See [LLM providers](providers.md) for the AI interpretation and the chat workflow.
