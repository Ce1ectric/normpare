# LLM providers

The interpretation stage (`deutung`) supports several backends, selected with `--provider`
(or `llm_provider` in a config file). Without a valid key the pipeline still runs
deterministically and exports the prompts for the chat workflow.

| Provider | Notes | Key |
|---|---|---|
| `anthropic` | default; needs the `anthropic` extra (`--extras anthropic`) | `ANTHROPIC_API_KEY` or a `.api_key` file |
| `openai` and OpenAI-compatible (`azure_openai`, `google`, `mistral`, `groq`, `together`, `openrouter`, `openai_compatible` for DeepSeek and others) | via the standard library, no extra package; `--model` required | the provider's API key env var |
| `ollama` | local server, runs without a key | — |
| `chat` | no API: prompts are exported for copy-paste into a chat UI; answers are injected back | — |

## Setting the key

```bash
export ANTHROPIC_API_KEY=sk-...        # or the provider's variable
# or put the key in a file ".api_key" in the working directory
```

The key is looked up in this order: the variable named by `llm_key_env` in a config
file; the provider's own variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`AZURE_OPENAI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`,
`TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_API_KEY`; `openai_compatible` has none);
the generic `LLM_API_KEY`; a file `.api_key` in the directory the command is started from;
and, for compatibility with the predecessor project, `../Pipeline/.api_key`. The file works
for every provider. Never commit it.

The default model is an Anthropic model for every provider, so name the model with
`--model` whenever the provider is not `anthropic`.

## Deterministic / chat workflow

```bash
# deterministic only (no AI):
normpare compare --old OLD --new NEW --out OUT --no-llm

# with AI interpretation:
export ANTHROPIC_API_KEY=sk-...
normpare compare --old OLD --new NEW --out OUT
```

Answers are cached by a hash over (model, prompt), so repeated runs are cheap, and every
interpretation carries an `evidence` quote that is checked against the source text
(hallucination guard). Failed checks land in a review queue.

## OpenAI

```bash
export OPENAI_API_KEY=sk-...
normpare compare --old OLD --new NEW --out OUT --provider openai --model gpt-4.1
```

Any OpenAI-compatible endpoint works the same way — `--provider google|mistral|groq|together|openrouter`,
or `--provider openai_compatible --base-url https://your-endpoint/v1` for anything else.
Azure needs `--provider azure_openai --base-url <deployment-url>` (the API version comes from
`llm_api_version` in the config).

## DeepSeek

DeepSeek speaks the OpenAI format, so it needs no provider of its own:

```bash
export LLM_API_KEY=sk-...
normpare compare --old OLD --new NEW --out OUT \
  --provider openai_compatible --base-url https://api.deepseek.com --model deepseek-v4-flash
```

`deepseek-v4-flash` **thinks by default** (reasoning effort `high`). normpare therefore
sends `"thinking": {"type": "disabled"}` with every OpenAI-compatible request: reasoning
tokens are billed as output, and while thinking is on the provider ignores `temperature`,
which the pipeline sets to 0 for reproducibility. Extracting structured JSON from a
prepared diff is not a task that needs a chain of thought.

Should an answer come back with a `reasoning_content` field anyway, the interpretation
stage counts it and says so in its summary (`Denkmodus: n Antwort(en) …`, and `field:
"reasoning"` in `pipeline_feedback`) — thinking that costs money should not be invisible.

`--batch` has no effect here: the message-batch API is Anthropic's, and every other
provider falls back to sequential calls.

## Ollama (local, free)

Run a model locally and point normpare at it — no key, no API cost:

```bash
ollama serve
ollama pull llama3.1:70b
normpare compare --old OLD --new NEW --out OUT --provider ollama --model llama3.1:70b
# custom host: --base-url http://my-host:11434/v1
```

## Choosing a model: measure, do not guess

The interpretation is only worth as much as its evidence. Every entry carries a quote that is
checked against the standard, and `deutung.json` reports `evidence_ok` per entry — so the
quality of a model is directly measurable:

```bash
python -c "import json;d=json.load(open('OUT/deutung.json'));x=[e for c in d['chapters'] for e in (c.get('interpretations') or [])];print(f\"evidence_ok: {100*sum(bool(e.get('evidence_ok')) for e in x)/max(len(x),1):.0f}%\")"
```

In practice this separates models. On a real standard revision, a frontier model reached
**99 %** verified evidence. A small, cheap model scored **21 %** under the guard of 0.1.x,
which checked the answer exactly as delivered — mostly because it put labels in front of
otherwise correct quotes; with today's quote extraction the same answers pass at about
**80 %**. It was also *more* confident while being less grounded, and misread editorial
rewording as substantive change. For regulatory work, prefer a strong model and cut cost
with `--batch` (~50 % cheaper) and the answer cache instead of with a weaker model.

`deepseek-v4-flash` is the exception worth knowing: over three complete standard
revisions (1,100 to 2,400 changes each) it reached **98.3 – 99.3 %** `evidence_ok` and
**97.6 – 98.3 %** `evidence_strict`, and every change sent to it was answered. The script
`tools/evidence_report.py` in the repository recomputes these numbers for any finished
run.

Note that `--batch` currently uses the Anthropic message-batch API; other providers fall back
to sequential calls.
