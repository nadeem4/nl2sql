# LLM Configuration

LLM configuration lives in `configs/llm.yaml` and defines the default model plus
optional per-agent overrides.

## File structure

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.2
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  indexing_enrichment:
    provider: openai
    model: gpt-5.2
    temperature: 0.0
    api_key: ${env:OPENAI_API_KEY}
```

## Fields

- `version`: schema version (currently `1`)
- `default`: LLM configuration used by default
- `agents`: optional map of agent name → LLM config override

Each LLM config supports:

- `provider`: LLM provider name — `openai` or `openrouter`
- `model`: model identifier
- `temperature`: float (defaults to `0.0`)
- `api_key`: optional; can use `${env:VAR}` or `${provider:key}`
- `base_url`: optional endpoint override (see [OpenRouter and other
  OpenAI-compatible endpoints](#openrouter-and-other-openai-compatible-endpoints))

## Providers

| provider | client | default endpoint |
| --- | --- | --- |
| `openai` | `ChatOpenAI` | OpenAI's own API |
| `openrouter` | `ChatOpenAI` | `https://openrouter.ai/api/v1` |

Any other value raises `ValueError: Unsupported LLM provider`.

## OpenRouter and other OpenAI-compatible endpoints

[OpenRouter](https://openrouter.ai) is an OpenAI-compatible gateway: a single
API key reaches Anthropic, Google, Meta and hundreds of other models. It needs
no extra dependency — the same `langchain-openai` client serves it with a
different `base_url`.

Models are named `<vendor>/<model>`, for example `anthropic/claude-sonnet-4.5`
or `google/gemini-2.5-pro`.

```yaml
version: 1
default:
  provider: openrouter
  model: anthropic/claude-sonnet-4.5
  temperature: 0.0
  api_key: ${env:OPENROUTER_API_KEY}
agents:
  indexing_enrichment:
    provider: openrouter
    model: anthropic/claude-sonnet-4.5
    temperature: 0.0
    api_key: ${env:OPENROUTER_API_KEY}
```

`base_url` is optional for `provider: openrouter` — it defaults to
`https://openrouter.ai/api/v1`. Setting it explicitly overrides that default,
which is what lets the same code path serve any other OpenAI-compatible
endpoint (vLLM, LiteLLM, a local proxy):

```yaml
version: 1
default:
  provider: openrouter
  model: meta-llama/Llama-3.1-70B-Instruct
  api_key: ${env:LOCAL_LLM_KEY}
  base_url: http://localhost:8000/v1
```

### OpenRouter does not cover embeddings

OpenRouter serves chat completions only. Embeddings are not routed through
`LLMRegistry`; they come from `EmbeddingService`, which is selected with the
`EMBEDDING_PROVIDER` environment variable:

```bash
OPENROUTER_API_KEY=sk-or-...   # chat completions
OPENAI_API_KEY=sk-...          # embeddings, when EMBEDDING_PROVIDER=openai (default)
```

Set `EMBEDDING_PROVIDER=local` to embed with the key-free ONNX
`all-MiniLM-L6-v2` model bundled with chromadb, and no `OPENAI_API_KEY` is needed
for the embedding step. Chat still requires a provider key, and switching
embedding providers requires a re-index. See
[System configuration → Embeddings](system.md#embeddings).

## Notes

- `agents` overrides allow you to use specialized models for tasks like
  indexing enrichment while keeping a single default model for query execution.
- `nl2sql setup` offers `openai` and `openrouter`. Only providers the engine can
  actually serve are offered, so a completed setup cannot fail on the first
  query with an unsupported-provider error.
