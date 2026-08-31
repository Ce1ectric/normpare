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
    [--provider NAME] [--model NAME] [--base-url URL] \
    [--language de] [--no-llm] [--batch] [--embed-fallback] [--embed-model NAME] \
    [--config file.json|file.toml]

# equivalent:
python -m normpare compare --old … --new … --out …
```

- `--old` / `--new` — the two versions (PDF or DOCX; a mix is fine).
- `--out` — the target directory for **all** output.
- `--no-llm` — produce only the deterministic outputs (no API key needed).
- `--provider` — one of `anthropic`, `openai`, `azure_openai`, `google`, `mistral`, `groq`,
  `together`, `openrouter`, `ollama`, `openai_compatible`, `chat`. Everything except
  `anthropic` speaks the OpenAI-compatible `/chat/completions` API; `chat` only exports the
  prompts for copy-paste. See [LLM providers](providers.md).
- `--base-url` — override the provider's API base URL. Required for `azure_openai` and
  `openai_compatible`, and the way to reach a custom Ollama host.
- `--batch` — send the interpretation as one Anthropic message batch: **~50 % cheaper**,
  processed asynchronously (see below). It has no effect on the other providers.
- `--embed-fallback` — optional embedding rescue pass (see below); off by default.
- `--config` — a JSON/TOML configuration file; it supplies the document metadata, and then
  `--old`/`--new` are optional.

## Reading the run

The run prints what it produced and, after an interpretation, how complete it is:

```
    Änderungen: 1899, vorgelegt 1897, gedeutet 1859 (97,9 %)
      ohne Antwort: 38   Literatur<Literatur (38)
    Kapitel in Teilanfragen: 7 (26 Zusatzanfragen)
    Tabellen gekürzt: 0 von 15
    Asset-Blöcke gekürzt: 0 von 47
```

`ohne Antwort` above zero means the model was shown changes it did not answer for; the
chapters are named so you can ask again for those alone. The same numbers are in
`deutung.json` under `coverage`; the component view carries the warning inline, and the CSV
gets a companion `Aenderungen_<run>_Abdeckung.txt` beside it.
[Concept](concept.md#coverage-what-was-asked-and-what-came-back) explains the three counts.

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

The paragraph alignment is deterministic by default: an anchor pass pairs paragraphs whose
text is identical once cosmetic differences are normalized away, a TF-IDF backend (word +
character n-grams) resolves the rest, and the result is byte-reproducible across runs. The
chapters above them are anchored on id and title first, then on similarity.

An **optional embedding rescue pass** (ADR-0001) can be switched on with `--embed-fallback`.
It runs *after* the deterministic pass and only ever refines it: paragraphs still left as
`removed` / `new` **in the same chapter** are re-paired with a German embedding model, so a
paragraph that was reworded in place so heavily that TF-IDF missed it becomes a single
*changed* entry instead of a separate removal + addition. The threshold is `tau_embed`
(default `0.82`, tuned for precision:
on a real-world standard revision it recovered about a dozen genuine in-place rewordings).
Encoded vectors are cached (`.vec_cache.npz`, keyed by model + normalized text), so the pass
is reproducible and cheap on re-runs.

The same flag also enables a **cross-chapter** rescue for passages the deterministic move
pass could not place. It is deliberately harder to satisfy, because at high similarity any
two technical paragraphs start to look alike: a pair is only accepted as a move when the
similarity reaches `tau_cross` (`0.90`), **every** parameter value of the old side appears
unchanged on the new side, and the pair is each other's best match. Below `tau_cross` a pair
is dropped outright; a pair that clears the threshold but fails one of the other two stays
`removed` + `new` and is noted as a candidate, so the rescue can never invent a move. It
needs the `embeddings` extra:

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
