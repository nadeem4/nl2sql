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

- `provider`: LLM provider name — `openai`, `anthropic`, `openrouter` or `ollama`
- `model`: model identifier (required; an empty model is rejected)
- `temperature`: float, or `null` to send no temperature at all (defaults to
  `0.0`; see [Temperature](#temperature))
- `api_key`: optional; can use `${env:VAR}` or `${provider:key}`
- `base_url`: optional endpoint override (see [Other OpenAI-compatible
  endpoints](#other-openai-compatible-endpoints))

## Providers and wire types

The engine is model-agnostic. A *provider* is a preset: an endpoint, the
environment variable its key comes from, and the *wire type* it speaks, which
is the HTTP protocol on the wire. Everything that differs between wire types
lives in one adapter per wire type, in `nl2sql/llm/wires/`:

| wire type | adapter | client | providers |
| --- | --- | --- | --- |
| `openai` | `wires/openai.py` | `ChatOpenAI` | `openai`, `openrouter`, `ollama`, and any OpenAI-compatible endpoint |
| `anthropic` | `wires/anthropic.py` | `ChatAnthropic` (the `anthropic` extra) | `anthropic` |

Each adapter owns four things, so neither the registry nor the nodes know which
wire they are on:

| | `openai` wire | `anthropic` wire |
| --- | --- | --- |
| **Client** | `ConfiguredChatOpenAI`, `seed=42` | `CachingChatAnthropic`, `max_tokens=16000` |
| **Structured output** | tool call (`function_calling`) | forced tool call (`function_calling`) |
| **Prompt caching** | nothing to mark: OpenAI caches long prefixes by itself | `cache_control` on the last system block |
| **Usage** | `prompt_tokens_details.cached_tokens` → cached; no cache writes reported | cache reads and writes, see [Query API → usage](../api/core/query.md) |

Both adapters also turn a model's HTTP 400 on `temperature` into the same
"set `temperature: null`" error (see [Temperature](#temperature)).

The providers:

| provider | wire type | default endpoint | API key |
| --- | --- | --- | --- |
| `openai` | `openai` | OpenAI's own API | required (`OPENAI_API_KEY`) |
| `anthropic` | `anthropic` | Anthropic's own API | required (`ANTHROPIC_API_KEY`) |
| `openrouter` | `openai` | `https://openrouter.ai/api/v1` | required (`OPENROUTER_API_KEY`) |
| `ollama` | `openai` | `http://localhost:11434/v1` | not required |

Any other value raises `ValueError: Unsupported LLM provider`, naming the valid
providers. Every agent picks its own provider, so one question can use several;
see [Per-node models](#per-node-models).

### Adding a provider

- **A provider that speaks an existing wire type** (a gateway, a hosted
  OpenAI-compatible service) is one entry in `PROVIDER_PRESETS` in
  `nl2sql/llm/registry.py`: its endpoint, key variable and `wire`. Nothing else
  changes. For a one-off endpoint you don't even need that: set `base_url` on an
  `openai` agent (see
  [Other OpenAI-compatible endpoints](#other-openai-compatible-endpoints)).
- **A new wire type** (Gemini, Bedrock) is one new adapter module in
  `nl2sql/llm/wires/` implementing the `Wire` protocol in `wires/base.py`
  (`build_client`, `structured_output_method`, `mark_cache`, `read_usage`), one
  entry in `WIRES` in `wires/__init__.py`, and presets that name it. Add a case
  to `CASES` in `tests/unit/test_llm_wire_contract.py`. The contract suite then
  runs every structured-output call, usage, cache and key test against it.

### OpenAI

`nl2sql setup` and `nl2sql demo` write `gpt-5.4` as the OpenAI default. On the
owner's account (probed 2026-09-20) it accepted `temperature: 0` with a limit of
500,000 tokens per minute, against 30,000 for the previous default `gpt-4o` -
less than one question with a single retry needs (about 33,500 tokens).

Every node asks for structured output as a tool call. The planner used to use
`langchain-openai`'s default, a `json_schema` response format; it now uses a
tool call like the other nodes, because that is what the openai wire's
adapter uses for every node, and what OpenRouter's and Ollama's models support
most widely.

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

### Anthropic (Claude)

Claude runs on Anthropic's own API through `ChatAnthropic`, not through an
OpenAI-compatibility shim: prompt caching and reliable structured output need
Anthropic's native API. `langchain-anthropic` is an optional extra:

```bash
pip install "nl2sql-engine[anthropic]"
```

Selecting `provider: anthropic` without it fails on the first LLM call with
`ValueError: ... Install the anthropic extra: pip install "nl2sql-engine[anthropic]"`,
and `nl2sql doctor` reports the missing extra.

```yaml
version: 1
default:
  provider: anthropic
  model: claude-opus-5
  temperature: null          # Claude Opus 5 and Sonnet 5 reject any temperature
  api_key: ${env:ANTHROPIC_API_KEY}
```

`nl2sql setup` and `nl2sql demo` write `claude-opus-5` as the Claude default,
with `temperature: null`. Current models:

| model | `temperature` | minimum cacheable prefix |
| --- | --- | --- |
| `claude-opus-5` | rejected (HTTP 400): use `null` | 512 tokens |
| `claude-sonnet-5` | rejected (HTTP 400): use `null` | 1,024 tokens |
| `claude-haiku-4-5` | accepted, e.g. `0.0` | 4,096 tokens |

These come from Anthropic's documented model rules, not from a probe on a real
account. A rejected temperature fails with the same "set `temperature: null`"
error as OpenAI (see [Temperature](#temperature)).

How the engine uses it:

- **Structured output** is a forced tool call (`with_structured_output`'s
  `function_calling` method), for every node. Anthropic's native JSON outputs
  (`output_config.format`) do not accept recursive schemas, and the planner's
  `PlanModel` is recursive.
- **Prompt caching.** The resolver, decomposer, planner and refiner send a
  stable system message and a variable human message. On the `anthropic` wire
  only, the last system
  block carries `cache_control: {"type": "ephemeral"}` (a 5-minute cache).
  Anthropic renders tools, then system, then messages, so the breakpoint caches
  the tool schema and the system prompt; the question follows it. The answer
  synthesizer sends no system message, so nothing is marked. Other providers
  send no `cache_control`.
- **Whether a prefix clears the minimum.** On Chinook the planner's cached
  prefix is its system prompt (about 3.5k tokens, most of it the retrieved
  schema) plus the `PlanModel` tool schema (about 2k): above every model's
  minimum. The decomposer's (about 0.7k of system prompt plus a 1.4k tool
  schema) and the refiner's (about 2.7k, no tool) clear Opus 5's 512 and
  Sonnet 5's 1,024 but not Haiku 4.5's 4,096, so on Haiku they are silently
  not cached. The resolver's answerability check (`datasourceresolver`) is
  marked the same way, but its prefix (about 260 tokens of system prompt plus a
  small tool schema) is below every Claude model's minimum, so it is never
  cached. The decomposer's prefix is the same for every question; the
  planner's and refiner's include the retrieved tables, so they hit when a
  sub-query retrieves the same tables again (a retry, or a similar question).
- **`max_tokens`** is 16,000 per call: every node call is non-streaming, and
  the SDK refuses a non-streaming call whose `max_tokens` could outlast its
  timeout.
- **Usage.** Cache reads and writes are reported as `cached_input_tokens` and
  `cache_write_input_tokens`, with each input token counted once (see
  [Query API → usage](../api/core/query.md)).

`nl2sql demo --record` cannot record Claude: the recording proxy and replay
speak the OpenAI wire format, so it asks for an OpenAI or OpenRouter key.

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
the engine's parameters of the time (`temperature=0`, `seed=42`, strict
`json_schema`; the planner now sends a tool call instead):

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
environment variable. Each node can be on a different provider, and so a
different wire type. For example, plan on Claude and run everything else on
OpenAI, with one cheaper node that only accepts its default temperature:

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.4
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  astplanner:
    provider: anthropic
    model: claude-opus-5
    temperature: null        # Claude Opus 5 rejects a temperature
    api_key: ${env:ANTHROPIC_API_KEY}
  answersynthesizer:
    provider: openai
    model: gpt-5-mini
    temperature: null        # gpt-5-mini rejects temperature=0
    api_key: ${env:OPENAI_API_KEY}
```

Here `datasourceresolver`, `decomposer` and `refiner` use `default`. Each node
keeps its own key, endpoint and cache behaviour: the planner's system prompt is
cache-marked for Anthropic, the others are sent unmarked to OpenAI. The model
each call actually used is recorded per node in `QueryResult.usage.calls` and
in a run trace's `llm.by_node` (see [Debugging](../observability/debugging.md)).

In the demo, the playground's **Settings** panel writes exactly these entries
into `configs/llm.demo.yaml`. Choosing a provider and a model for a step adds
an `agents:` entry with `provider`, `model` and the temperature that model
takes: `0.0`, or `null` for `gpt-5.5`, `gpt-5-mini`, `claude-opus-5` and
`claude-sonnet-5`. A step on the default's provider takes the default's
`base_url` and `api_key` reference; a step on another provider gets that
provider's own `${env:...}` reference and no `base_url`. Choosing "Default"
removes the entry. The running engine reloads the file, so the change applies
to the next question. The panel offers the providers and models in
`VERIFIED_MODELS` (`nl2sql/llm/providers.py`), OpenAI and Anthropic for
now, and a provider only once its key is saved. See
[the Settings page](../getting_started/demo.md#the-settings-page).

When `nl2sql demo` picks replay or live mode at start-up it points `default`
at the chosen provider. In replay and record mode every `agents:` entry
follows, because one fake or proxy serves every call. In live mode a step
keeps the provider it was given; only an entry with no provider, or one left
on the replay endpoint, follows the default. Moving an entry between OpenAI
and Anthropic replaces a model the new provider does not serve (a `gpt-` model
on Anthropic, a `claude-` one on OpenAI) with that provider's default and its
temperature. Every provider key saved in `.env.demo` is loaded at start-up, so
each step finds its own.

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

None of Anthropic, OpenRouter or Ollama covers embeddings — they are not routed through
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
- `nl2sql setup` prompts for `openai`, `anthropic` and `openrouter`, the hosted
  providers it can also collect a key for; `setup --api-key` picks the provider
  from the key's shape (`sk-ant-` is Anthropic, `sk-or-` is OpenRouter, anything
  else is OpenAI). To use Ollama, edit `configs/llm.yaml` as shown above — no
  key prompt applies.
