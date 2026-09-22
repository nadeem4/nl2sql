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
- `temperature`: float, or `null` to send no temperature at all (defaults to
  `0.0`; see [Temperature](#temperature))
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

`nl2sql setup` and `nl2sql demo` write `gpt-5.4` as the OpenAI default. On the
owner's account (probed 2026-09-20) it accepted `temperature: 0` with a limit of
500,000 tokens per minute, against 30,000 for the previous default `gpt-4o` -
less than one question with a single retry needs (about 33,500 tokens).

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.4
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  indexing_enrichment:
    provider: openai
    model: gpt-5.4
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

## Temperature

Each agent sends exactly the `temperature` its config names, and `seed=42`:

- Omitted, it is `0.0`, sent with every call. This reduces variance; it does
  not make output reproducible (see [Determinism](../architecture/determinism.md)).
- `temperature: null` sends **no temperature parameter at all**, so the model
  uses its own default.

Use `null` for models that accept only their default. Probed on 2026-09-20 with
the engine's own parameters (`temperature=0`, `seed=42`, strict `json_schema`):

| model | `temperature: 0` |
| --- | --- |
| `gpt-5.4`, `gpt-5.4-mini`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o` | accepted |
| `gpt-5.5`, `gpt-5-mini` | rejected: HTTP 400 *"Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported."* |

The rejected models accepted the same call without `temperature` (still with
`seed=42`), so only the temperature needs to go. When a model rejects it, the
first LLM call fails with an error that names the model and the agent and says
what to set, instead of the provider's raw response:

```text
ValueError: Model 'gpt-5.5' (LLM agent 'default') rejected the temperature
parameter (HTTP 400: Unsupported value: 'temperature' does not support 0.0 with
this model. Only the default (1) value is supported.) Set 'temperature: null'
for agent 'default' in the LLM config file so no temperature is sent to this
model.
```

Nothing is retried without the parameter: what the config says is what is sent.

!!! note "langchain-openai would drop it silently"
    `langchain-openai` removes any temperature other than `1` from a model whose
    name starts with `gpt-5` before the request is built, so `gpt-5.4` would
    never receive the `0.0` it accepts. The registry sets the configured value
    after the client is constructed, so the config - not the model name -
    decides what is sent.

## Per-node models

The five pipeline nodes that call a model each ask the registry for their own
agent name, and fall back to `default` when it is not configured:

| agent name | node |
| --- | --- |
| `datasourceresolver` | checks that the connected data can answer the question at all (one short call; refuses with `QUESTION_NOT_ANSWERABLE`) |
| `decomposer` | splits the question into sub-queries |
| `astplanner` | writes the query plan (the largest prompt: it carries the schema) |
| `refiner` | explains a failed plan so the planner can retry |
| `answersynthesizer` | writes the final answer from the rows |
| `indexing_enrichment` | writes schema descriptions during `nl2sql index` (optional) |

The key under `agents` is the agent's name; a `name:` field is not needed. Each
entry is a complete agent config - nothing is inherited from `default` - so
`provider` and `model` are required, `temperature` falls back to `0.0` (not to
the default agent's value), and a missing `api_key` falls back to the provider's
environment variable. For example, keep `gpt-5.4` for planning and run the
cheaper nodes on other models, one of which only accepts its default
temperature:

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.4
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  decomposer:
    provider: openai
    model: gpt-5.4-mini
    temperature: 0.0
    api_key: ${env:OPENAI_API_KEY}
  answersynthesizer:
    provider: openai
    model: gpt-5-mini
    temperature: null        # gpt-5-mini rejects temperature=0
    api_key: ${env:OPENAI_API_KEY}
```

Here `astplanner` and `refiner` use `default`. The model each call actually
used is recorded per node in `QueryResult.usage.calls` and in a run trace's
`llm.by_node` (see [Debugging](../observability/debugging.md)).

In the demo, the playground's **Settings** panel writes exactly these entries
into `configs/llm.demo.yaml`: choosing a model for a step adds an `agents:`
entry with the default's provider, `base_url` and `api_key` reference, the
chosen model, and `temperature: 0.0`, or `temperature: null` for `gpt-5.5` and
`gpt-5-mini`; choosing "Default" removes the entry. The running engine reloads
the file, so the change applies to the next question. The panel offers only
the models in `VERIFIED_MODELS` (`nl2sql/cli/common/api_key.py`), OpenAI only
for now. See [the settings panel](../getting_started/demo.md#the-settings-panel).

Because a demo step differs from the default only in its model, `nl2sql demo`
points every `agents:` entry at the same provider, endpoint and key variable as
`default` when it picks replay or live mode at start-up.

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
  still run key-free work such as indexing with `EMBEDDING_PROVIDER=local`. That
  holds for indexing because the `indexing_enrichment` pass is genuinely
  optional - it is skipped, not merely deferred to a later failure.
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

- `agents` overrides give individual nodes, or indexing enrichment, their own
  model and temperature; see [Per-node models](#per-node-models).
- `indexing_enrichment` is optional. Omit it, or leave its key unset, and
  `nl2sql index` still works - the schema is indexed without LLM-written
  descriptions.
- `nl2sql setup` prompts for `openai` and `openrouter`, the two hosted providers
  it can also collect a key for. To use Ollama, edit `configs/llm.yaml` as shown
  above — no key prompt applies.
