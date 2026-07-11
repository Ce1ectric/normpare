# LLM providers

The interpretation stage (`deutung`) supports several backends, selected with `--provider`
(or `llm_provider` in a config file). Without a valid key the pipeline still runs
deterministically and exports the prompts for the chat workflow.

| Provider | Notes | Key |
|---|---|---|
| `anthropic` | default; needs the `anthropic` extra (`--extras anthropic`) | `ANTHROPIC_API_KEY` or a `.api_key` file |
| `openai` and OpenAI-compatible (`azure`, `google`, `mistral`, `groq`, `openrouter`, …) | via the standard library, no extra package | the provider's API key env var |
| `ollama` | local server, runs without a key | — |
| `chat` | no API: prompts are exported for copy-paste into a chat UI; answers are injected back | — |

## Setting the key

```bash
export ANTHROPIC_API_KEY=sk-...        # or the provider's variable
# or put the key in a file ".api_key" in the working directory
```

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
python -c "import json;d=json.load(open('OUT/deutung.json'));x=[e for c in d['chapters'] for e in (c.get('interpretations') or [])];print(f\"evidence_ok: {100*sum(bool(e.get('evidence_ok')) for e in x)/len(x):.0f}%\")"
```

In practice this separates models sharply. On a real standard revision, a frontier model
reached **99 %** verified evidence, while a small, cheap model reached only **21 %** —
it paraphrased instead of quoting, was *more* confident while being less grounded, and
misread editorial rewording as substantive change. For regulatory work, prefer a strong model
and cut cost with `--batch` (~50 % cheaper) and the answer cache instead of with a weaker model.

Note that `--batch` currently uses the Anthropic message-batch API; other providers fall back
to sequential calls.
