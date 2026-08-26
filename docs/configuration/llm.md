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

- `provider`: LLM provider name — `openai`, `openrouter` or `ollama`
- `model`: model identifier (required; an empty model is rejected)
- `temperature`: float (defaults to `0.0`)
- `api_key`: optional; can use `${env:VAR}` or `${provider:key}`
- `base_url`: optional endpoint override (see [Other OpenAI-compatible
  endpoints](#other-openai-compatible-endpoints))

## Providers

All three providers speak the OpenAI wire protocol, so all three are served by
the same `ChatOpenAI` client with a different base URL — no extra dependency is
involved. The provider name only selects a preset:

| provider | default endpoint | API key |
| --- | --- | --- |
| `openai` | OpenAI's own API | required (`OPENAI_API_KEY`) |
| `openrouter` | `https://openrouter.ai/api/v1` | required (`OPENROUTER_API_KEY`) |
| `ollama` | `http://localhost:11434/v1` | not required |

Any other value raises `ValueError: Unsupported LLM provider`, naming the valid
providers.

### OpenAI

```yaml
version: 1
default:
  provider: openai
  model: gpt-4o
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  indexing_enrichment:
    provider: openai
    model: gpt-4o
    temperature: 0.0
    api_key: ${env:OPENAI_API_KEY}
```

### OpenRouter

[OpenRouter](https://openrouter.ai) is an OpenAI-compatible gateway: a single
API key reaches Anthropic, Google, Meta and hundreds of other models. Models are
named `<vendor>/<model>`, for example `anthropic/claude-sonnet-4.5` or
`google/gemini-2.5-pro`.

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

### Ollama

[Ollama](https://ollama.com) exposes an OpenAI-compatible API on
`http://localhost:11434/v1`, so it needs no `api_key` and no `base_url`: start
the daemon, pull a model, and point `configs/llm.yaml` at it.

```yaml
version: 1
default:
  provider: ollama
  model: llama3.1
  temperature: 0.0
agents:
  indexing_enrichment:
    provider: ollama
    model: llama3.1
    temperature: 0.0
```

Set `base_url` if the daemon is not on `localhost:11434` (a remote box, or a
different port).

!!! warning "Ollama is supported at the transport level, not guaranteed end to end"
    The pipeline drives every model through `with_structured_output`, and the
    AST planner's target is `PlanModel` — a *recursive* Pydantic schema whose
    `Expr` references itself. That is a demanding structured-output target, and
    small local models often handle it poorly: expect malformed plans, empty
    fields or repeated refiner loops depending on the model you choose. Whether
    a given local model works is a property of that model, not of the transport.
    Prefer the largest instruct model with reliable JSON-schema/tool-calling
    support that your hardware allows, and validate against your own questions
    before relying on it.

    Ollama also does not make the demo run fully offline: embeddings are a
    separate path (see [below](#embeddings-are-a-separate-path)), and the demo's
    other steps are unchanged.

## Other OpenAI-compatible endpoints

A config-supplied `base_url` always overrides the provider preset. That is what
lets the same code path serve any other OpenAI-compatible endpoint — vLLM,
LiteLLM, a local proxy — without a new provider:

```yaml
version: 1
default:
  provider: openai
  model: meta-llama/Llama-3.1-70B-Instruct
  api_key: ${env:LOCAL_LLM_KEY}
  base_url: http://localhost:8000/v1
```

Pick the provider whose credential behaviour matches the endpoint: `openai` or
`openrouter` if the endpoint expects a key, `ollama` if it accepts any key.

## Clients are built on first use

Registering an agent validates its configuration; the client itself is built the
first time that agent's LLM is requested. Two consequences:

- Building an `NL2SQLContext` (which `nl2sql setup`, `nl2sql index` and the API
  all do) no longer requires a chat provider key. A machine with no key can
  still run key-free work such as indexing with `EMBEDDING_PROVIDER=local`.
- A missing or wrong key surfaces on the **first LLM call**, not at startup. The
  error names the agent, the provider and the environment variable to set:

  ```text
  ValueError: LLM agent 'default' uses provider 'openai', which requires an API
  key, but none could be resolved. Set OPENAI_API_KEY in the environment, or give
  the agent an 'api_key' in configs/llm.yaml.
  ```

Genuine misconfiguration still fails immediately at registration: an unknown
provider, or an agent with no model.

## Embeddings are a separate path

Neither OpenRouter nor Ollama covers embeddings — they are not routed through
`LLMRegistry` at all. Embeddings come from `EmbeddingService`, selected with the
`EMBEDDING_PROVIDER` environment variable:

```bash
OPENROUTER_API_KEY=sk-or-...   # chat completions
OPENAI_API_KEY=sk-...          # embeddings, when EMBEDDING_PROVIDER=openai (default)
```

Set `EMBEDDING_PROVIDER=local` to embed with the key-free ONNX
`all-MiniLM-L6-v2` model bundled with chromadb, and no `OPENAI_API_KEY` is needed
for the embedding step. Switching embedding providers requires a re-index. See
[System configuration → Embeddings](system.md#embeddings).

## Notes

- `agents` overrides allow you to use specialized models for tasks like
  indexing enrichment while keeping a single default model for query execution.
- `nl2sql setup` prompts for `openai` and `openrouter`, the two hosted providers
  it can also collect a key for. To use Ollama, edit `configs/llm.yaml` as shown
  above — no key prompt applies.
