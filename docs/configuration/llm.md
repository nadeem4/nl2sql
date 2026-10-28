# LLM Configuration

The `llm` section of [`nl2sql.yaml`](system.md) names the model every agent
uses, and optional per-agent overrides. `nl2sql init` writes it to `llm.yaml`
with the provider's default model and no key.

```yaml
# llm.yaml
provider: openai
model: gpt-5.4
temperature: 0.0
# api_key is optional: without it the provider's standard variable
# (OPENAI_API_KEY) is read. Write it to read another one:
# api_key: "${env:TEAM_OPENAI_KEY}"
agents:
  ast_planner:         { provider: anthropic, model: claude-opus-5, temperature: null }
  profiler: { model: gpt-5.4-mini }
prices:
  gpt-4o: { input: 2.5, cached_input: 1.25, output: 10 }
```

## Fields

The section is flat: its keys are the top-level agent's.

| Field | Default | Meaning |
| --- | --- | --- |
| `provider` | required | `openai`, `anthropic`, `openai_compatible`, its presets `openrouter` and `ollama`, or a provider a plugin registers; see [Providers](#providers-and-wire-types). A misspelt or uninstalled one is a `ConfigError` with "did you mean". |
| `model` | required | The model identifier. In Python it may be a LangChain chat model object instead (`LLM(model=ChatOpenAI(...))`), with no `provider`. |
| `temperature` | `0.0` | A float, or `null` to send no temperature at all; see [Temperature](#temperature). |
| `api_key` | none | Without it, the provider's standard variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) is read. With it, a `${env:VAR}` or `${provider:key}` reference (or a literal, which is not recommended). The reference is resolved when the client is built, never at load. |
| `base_url` | the provider's | An endpoint override. Required for `openai_compatible`; see [Any other OpenAI-style server](#any-other-openai-style-server-openai_compatible). |
| `structured_output` | the provider's | `tool_calling`, `json_schema` or `json_mode`: how structured answers are asked for. See [Structured output](#structured-output). |
| `parse_retries` | `1` | How many times a structured answer that does not parse is asked for again, with what was wrong. `0` asks once. See [Parse-and-retry](#parse-and-retry). |
| `max_tokens` | the provider's | The most tokens one answer may use. Unset, Anthropic gets 16,000 and the other providers send none. |
| `timeout_s` | the client library's | Seconds one request may take. |
| `max_retries` | the client library's | Network and rate-limit retries, done by the client library. |
| `headers` | `{}` | HTTP headers sent with every request, such as a gateway's team or routing header. A value may be a `${env:VAR}` or `${provider:key}` reference, resolved when the client is built. Values never print: not in a `repr`, a log, `engine.llm.list()` or the cache key. |
| `agents` | `{}` | Per-node overrides, keyed by node name; see [Per-node models](#per-node-models). |
| `prices` | `{}` | Per-model prices per million tokens; see [Prices](#prices). |

`engine.check()` reports every agent whose key does not resolve, without
calling a model.

## Providers and wire types

The engine is model-agnostic. A *provider* is a preset: an endpoint, the
environment variable its key comes from, and the *wire type* it speaks, which
is the HTTP protocol on the wire. Everything that differs between wire types
lives in one adapter per wire type, in `nl2sql/llm/wires/`:

| wire type | adapter | client | providers |
| --- | --- | --- | --- |
| `openai` | `wires/openai.py` | `ChatOpenAI` | `openai`, `openai_compatible`, `openrouter`, `ollama` |
| `anthropic` | `wires/anthropic.py` | `ChatAnthropic` (the `anthropic` extra) | `anthropic` |

Each adapter owns four things, so neither the registry nor the nodes know which
wire they are on:

| | `openai` wire | `anthropic` wire |
| --- | --- | --- |
| **Client** | `ConfiguredChatOpenAI`, `seed=42` | `CachingChatAnthropic`, `max_tokens=16000` unless the agent sets one |
| **Structured output** | the agent's `structured_output` | the agent's `structured_output` |
| **Prompt caching** | nothing to mark: OpenAI caches long prefixes by itself | `cache_control` on the last system block |
| **Usage** | `prompt_tokens_details.cached_tokens` → cached; no cache writes reported | cache reads and writes, see [Query API → usage](../api/core/query.md) |

Both adapters also turn a model's HTTP 400 on `temperature` into the same
"set `temperature: null`" error (see [Temperature](#temperature)).

The built-in providers are deliberately few: `openai`, `anthropic` and
`openai_compatible`. `openrouter` and `ollama` are named presets of
`openai_compatible`: a fixed endpoint and key variable, and no code of their
own.

| provider | wire type | key variable | `base_url` | default `structured_output` |
| --- | --- | --- | --- | --- |
| `openai` | `openai` | `OPENAI_API_KEY` | optional | `tool_calling` |
| `anthropic` | `anthropic` | `ANTHROPIC_API_KEY` | optional | `tool_calling` |
| `openai_compatible` | `openai` | none: write `api_key` (usually `${env:...}`) if the server needs one | **required** | `json_schema` |
| `openrouter` | preset of `openai_compatible` | `OPENROUTER_API_KEY` | fixed: `https://openrouter.ai/api/v1` | `tool_calling` |
| `ollama` | preset of `openai_compatible` | none | fixed: `http://localhost:11434/v1` (a `base_url` may override it) | `tool_calling` |

There is no Azure provider: Azure OpenAI's v1 endpoint is `openai_compatible`
(see [the `base_url` table](#any-other-openai-style-server-openai_compatible)).
Any other name raises `ValueError: Unsupported LLM provider`, naming the valid
providers. Every agent picks its own provider, so one question can use several;
see [Per-node models](#per-node-models).

### Adding a provider

- **Another OpenAI-style server** needs no new provider: use
  `openai_compatible` with its `base_url` (see
  [Any other OpenAI-style server](#any-other-openai-style-server-openai_compatible)).
- **Anything else** (Azure with Entra ID sign-in, Bedrock, Gemini) is a plugin:
  a class implementing `LLMProvider` (`nl2sql/llm/providers.py`, interface
  version 2: `interface_version = 2`, `label`, `api_key_env`, `requires`,
  `install_hint`, `structured_output` and `create(agent)`), registered with
  `registry.add_llm` or under the `nl2sql.llms` entry point group. A provider
  built for another interface version is rejected when it is loaded.
- **A new wire type** inside the engine (Gemini, Bedrock) is one new adapter module in
  `nl2sql/llm/wires/` implementing the `Wire` protocol in `wires/base.py`
  (`build_client`, `structured_output_method`, `mark_cache`, `read_usage`), one
  entry in `WIRES` in `wires/__init__.py`, and presets that name it. Add a case
  to `CASES` in `tests/unit/test_llm_wire_contract.py`. The contract suite then
  runs every structured-output call, usage, cache and key test against it.

### OpenAI

`nl2sql init` and `nl2sql demo` write `gpt-5.4` as the OpenAI default. On the
owner's account (probed 2026-09-20) it accepted `temperature: 0` with a limit of
500,000 tokens per minute, against 30,000 for the previous default `gpt-4o` -
less than one question with a single retry needs (about 33,500 tokens).

Every node asks for structured output as a tool call, OpenAI's default
`structured_output`: it is what OpenRouter's and Ollama's models support most
widely too. See [Structured output](#structured-output).

```yaml
provider: openai
model: gpt-5.4
temperature: 0.0
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
provider: anthropic
model: claude-opus-5
temperature: null          # Claude Opus 5 and Sonnet 5 reject any temperature
```

`nl2sql init --provider anthropic` and `nl2sql demo` write `claude-opus-5` as the Claude default,
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
  `function_calling` method, `structured_output: tool_calling`), for every node. Anthropic's native JSON outputs
  (`output_config.format`) do not accept recursive schemas, and the planner's
  `PlanModel` is recursive; neither does strict tool use (`strict: true`).
  Claude now and then nests a tool call's arguments one level down under a key
  of its own choosing (`{"input": {...}}`, `{"query": {...}}`, `{"dtype": {...}}`).
  Recording runs on `claude-opus-5` failed the decomposer this way on up to 9
  of 20 questions, and no other node. The root cause was the decomposer's tool:
  it went out with an empty description and no field descriptions, while its
  prompt asked for "JSON only" rather than a tool call. `DecomposerResponse`
  now describes itself and each top-level field ("call this tool with the
  decomposition itself as the arguments ... do not wrap them"), and the prompt
  asks for the tool call. As a safety net the wire also unwraps that shape
  before any node parses it: a dict with exactly one key the schema does not
  declare, holding an object with every required field and at least one
  declared one. Any other malformed answer still fails validation as before.
  A new node's response model should carry a docstring and field descriptions
  for the same reason.
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
  schema) plus the `PlannerAnswer` tool schema (about 2k): above every model's
  minimum. The decomposer's (about 0.7k of system prompt plus a 1.4k tool
  schema) and the refiner's (about 2.7k, no tool) clear Opus 5's 512 and
  Sonnet 5's 1,024 but not Haiku 4.5's 4,096, so on Haiku they are silently
  not cached. The resolver's answerability check (`datasourceresolver`) is
  marked the same way, but its prefix (about 260 tokens of system prompt plus a
  small tool schema) is below every Claude model's minimum, so it is never
  cached. The decomposer's prefix is the same for every question; the
  planner's and refiner's include the retrieved tables, so they hit when a
  sub-query retrieves the same tables again (a retry, or a similar question).
- **`max_tokens`** is 16,000 per call unless the agent sets `max_tokens`:
  every node call but the streamed answer is non-streaming, and the SDK
  refuses a non-streaming call whose `max_tokens` could outlast its timeout.
- **Usage.** Cache reads and writes are reported as `cached_input_tokens` and
  `cache_write_input_tokens`, with each input token counted once (see
  [Query API → usage](../api/core/query.md)). A cache write costs 1.25x the
  model's `input` rate (Anthropic's price for a 5-minute write, the only kind
  the engine asks for): the price table's Claude rows say so, and an
  `llm.prices` row without its own `cache_write` gets it from the wire.

`nl2sql demo --record` records Claude too: with an Anthropic key the recording
proxy speaks Anthropic's Messages API to `https://api.anthropic.com`, and stores
each answer under the same name and question an OpenAI one would get, so replay
(which answers on the OpenAI wire) serves it unchanged. The hosted demo's shipped
recordings are made this way; see
[Hosted demo](../deployment/hosted-demo.md#recorded-answers-without-a-key).

### OpenRouter

A preset of `openai_compatible`. [OpenRouter](https://openrouter.ai) is an OpenAI-compatible gateway: a single
API key reaches Anthropic, Google, Meta and hundreds of other models. Models are
named `<vendor>/<model>`, for example `anthropic/claude-sonnet-4.5` or
`google/gemini-2.5-pro`.

```yaml
provider: openrouter
model: anthropic/claude-sonnet-4.5
temperature: 0.0
```

### Ollama

A preset of `openai_compatible`. [Ollama](https://ollama.com) exposes an
OpenAI-compatible API on `http://localhost:11434/v1`, so it needs no `api_key`
and no `base_url`: start
the daemon, pull a model, and point `llm.yaml` at it.

```yaml
provider: ollama
model: llama3.1
temperature: 0.0
```

Set `base_url` if the daemon is not on `localhost:11434` (a remote box, or a
different port).

!!! warning "Ollama is supported at the transport level, not guaranteed end to end"
    The pipeline drives every model through `with_structured_output`, and the
    AST planner's target is `PlannerAnswer`, whose `PlanModel` is a *recursive* Pydantic schema whose
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

The six pipeline nodes that call a model, the profiler's drafts, eval
seeding and the conversation each have an agent. An agent not under `agents` runs on the top level:

| `agents` key | node |
| --- | --- |
| `curated_matcher` | picks the verified query that answers the question, if any (one short call; only when [curated answers](curated.md) are configured and a candidate clears the floor) |
| `datasource_resolver` | checks that the connected data can answer the question at all (one short call; refuses with `QUESTION_NOT_ANSWERABLE`) |
| `decomposer` | splits the question into sub-queries |
| `ast_planner` | writes the query plan (the largest prompt: it carries the schema) |
| `refiner` | explains a failed plan so the planner can retry |
| `answer_synthesizer` | writes the final answer from the rows |
| `profiler` | drafts table and column descriptions as proposals for review, never written into the index or the schema (optional; `nl2sql index` calls no model; only `nl2sql profile --draft` or `profile(draft=True)` runs it, under `profiler.max_cost_usd`, and its model must have a price in `llm.prices`) |
| `eval_seeder` | drafts eval questions with typed plans during `nl2sql eval seed` ([Benchmark your own database](../evals/seeding.md)) |
| `conversation` | leads a conversation turn: reads the history and either replies or calls a tool, one call per step (release 0.9, [Wave C](../architecture/wave_c.md); `engine.conversations` arrives with C1). A small model is enough. A config that pins every agent's model should add it. |

Any other key is an error with "did you mean". An agent takes every field it
sets (`provider`, `model`, `temperature`, `api_key`, `base_url`,
`structured_output`, `parse_retries`, `max_tokens`, `timeout_s`, `max_retries`, `headers`) and
inherits the rest from the top level. Inheritance follows which fields were written, so
`temperature: null` on an agent still means "send none". Each node can be on a
different provider, and so a different wire type. For example, plan on Claude
and run everything else on OpenAI, with one cheaper node that only accepts its
default temperature:

```yaml
provider: openai
model: gpt-5.4
temperature: 0.0
agents:
  ast_planner:
    provider: anthropic
    model: claude-opus-5
    temperature: null        # Claude Opus 5 rejects a temperature
  answer_synthesizer:
    model: gpt-5-mini        # on OpenAI, inherited
    temperature: null        # gpt-5-mini rejects temperature=0
```

Here `curated_matcher`, `datasource_resolver`, `decomposer` and `refiner` run on the top level. With no `api_key`, the
planner reads `ANTHROPIC_API_KEY` and the others `OPENAI_API_KEY`. Each node
keeps its own key, endpoint and cache behaviour: the planner's system prompt is
cache-marked for Anthropic, the others are sent unmarked to OpenAI. The model
each call actually used is recorded per node in `QueryResult.usage.calls` and
in a run trace's `llm.by_node` (see [Debugging](../observability/debugging.md)).

In the demo, the playground's **Settings** page writes exactly these entries
into the demo folder's `llm.yaml`. Choosing a provider and a model for a step adds
an `agents:` entry with `provider`, `model` and the temperature that model
takes: `0.0`, or `null` for `gpt-5.5`, `gpt-5-mini`, `claude-opus-5` and
`claude-sonnet-5`. A step on the default's provider takes the default's
`base_url` and `api_key`; a step on another provider names no key (its
standard variable is read) and no `base_url`. A key saved on the page goes to
the demo's `.env`. Choosing "Default" removes the entry. The running engine
reloads its `llm` section from `nl2sql.yaml`, so the change applies to the
next question. The page offers the providers and models in
`VERIFIED_MODELS` (`nl2sql/llm/providers.py`), OpenAI and Anthropic for
now, and a provider only once its key is saved. See
[the Settings page](../getting_started/demo.md#the-settings-page).

The playground's **Pipeline** page reads the same configuration back: it lists
every step of a run and, for each of the six a model decides, the provider and
model it is set to use, then what it actually spent once a question has been
asked. It is served by `GET /api/pipeline` from `engine.about()`, whose steps
(`nl2sql.pipeline.steps`) take their six model steps from `LLM_AGENTS` rather
than repeating the table above. See [the Pipeline page](../getting_started/demo.md#the-pipeline-page).

On the **hosted demo** (`nl2sql demo --hosted`) the same choice is offered and
nothing is written at all. The visitor keeps one key per provider and a model
per step in their own browser tab, and both travel with each question: a key
per provider in a header of its own, and the choices in one compact
`X-NL2SQL-Models` header, `{"astplanner": "anthropic:claude-opus-5"}` (the header names a step by
its registry agent name, `LLM_AGENTS` in `nl2sql/llm/providers.py`). The
server validates every name against `LLM_AGENTS` and `VERIFIED_MODELS`, builds
each step's client for that one request from the key for the provider it names,
caches none of them, and refuses with `400` -- naming the step and the
provider -- when a step is on a provider the request brought no key for. See
[Hosted demo](../deployment/hosted-demo.md).

When `nl2sql demo` picks replay or live mode at start-up it points the top
level of `llm.yaml` at the chosen provider. In replay and record mode every
`agents:` entry follows, with the local replay server or recording proxy as
`base_url` and the placeholder `api_key` (`replay`, `proxy`) it accepts,
because one fake or proxy serves every call; no key goes into the
environment. In live mode a step keeps the provider it was given; only an
entry with no provider, or one left on the replay endpoint, follows the top
level, and no `api_key` is written. Moving an entry between OpenAI and
Anthropic replaces a model the new provider does not serve (a `gpt-` model on
Anthropic, a `claude-` one on OpenAI) with that provider's default and its
temperature. Every provider key saved in the demo's `.env` is loaded at
start-up, so each step finds its own.

## Any other OpenAI-style server (`openai_compatible`)

`openai_compatible` is any server with an OpenAI-style chat API. Its
`base_url` is required, and it reads no key variable: write `api_key` (usually
`${env:...}`) if the server needs one. Without one it sends a placeholder,
because the OpenAI client refuses to start without a key; it never sends
`OPENAI_API_KEY`, which belongs to OpenAI.

```yaml
# llm.yaml
provider: openai_compatible
base_url: http://vllm:8000/v1
model: qwen3-32b
api_key: "${env:VLLM_KEY}"          # optional for this provider
structured_output: json_schema      # the default for openai_compatible
max_tokens: 8000
timeout_s: 60
max_retries: 2
headers: { X-Team: analytics }
agents:
  answer_synthesizer:
    provider: openai_compatible     # Azure OpenAI's v1 endpoint
    base_url: https://my-resource.openai.azure.com/openai/v1/
    api_key: "${env:AZURE_OPENAI_API_KEY}"
    model: gpt-5-4-prod             # the Azure deployment name
    structured_output: tool_calling
```

| Server | `provider` | `base_url` | `model` | Notes |
| --- | --- | --- | --- | --- |
| Azure OpenAI (v1 endpoint) | `openai_compatible` | `https://<resource>.openai.azure.com/openai/v1/` | the deployment name | `api_key: "${env:AZURE_OPENAI_API_KEY}"`; set `structured_output: tool_calling` |
| vLLM | `openai_compatible` | `http://<host>:8000/v1` | the served model, e.g. `qwen3-32b` | `json_schema` works with its guided decoding |
| Ollama | `ollama` | preset: `http://localhost:11434/v1`; set it for another host | a pulled model, e.g. `llama3.1` | no key |
| LiteLLM proxy | `openai_compatible` | `http://<host>:4000/v1` | a model name the proxy routes | `api_key` is the proxy's key, if it has one |
| OpenRouter | `openrouter` | preset: `https://openrouter.ai/api/v1` | `<vendor>/<model>` | `OPENROUTER_API_KEY` |

LM Studio, llama.cpp's server and a company gateway are `openai_compatible`
too.

## Structured output

Every node asks for a typed answer (the plan, the decomposition, the answer).
`structured_output` says how:

| `structured_output` | What is sent | Use it for |
| --- | --- | --- |
| `tool_calling` | a forced tool call whose arguments are the answer | OpenAI, Anthropic, Azure, OpenRouter, Ollama |
| `json_schema` | a response format carrying the JSON schema, which the server decodes against | local servers with schema-guided decoding (vLLM, LM Studio) |
| `json_mode` | JSON mode, with the schema added to the prompt, since JSON mode alone does not carry it | a server with neither of the above |

Unset, an agent uses its provider's default (the table under
[Providers](#providers-and-wire-types)). The method is never switched
automatically: the agent's setting is what runs.

### Parse-and-retry

An answer that does not parse (the parser fails, it does not validate against
the schema, or the model made no tool call) is asked for again, up to the
agent's `parse_retries` times (default `1`). Each retry adds the model's answer
and what was wrong with it to the messages:

```text
Your answer did not match the schema: `steps[0].op`: Field required. Answer again with only PlanModel.
```

If the last answer still does not parse, the node reports
`MODEL_OUTPUT_INVALID`, whose message names the agent and suggests another
`structured_output` or a stronger model; see
[Error handling](../observability/error-handling.md#model-output-invalid).

- Each retry is a model call of its own, so it is in the run's trace (one more
  entry in the node's `llm_calls`) and in its token usage and cost.
- A streamed answer (the answer synthesizer's, when the run streams) that does
  not parse is retried without streaming.
- `parse_retries` is separate from `max_retries`, which is the client
  library's retry of network errors and rate limits.

## Timeouts, retries and headers

`timeout_s`, `max_retries` and `headers` go to the client library
(`ChatOpenAI` or `ChatAnthropic`) as its `timeout`, `max_retries` and
`default_headers`; `max_tokens` as its `max_tokens`. Left unset, the client
library's own default holds (for Anthropic's `max_tokens`, 16,000). These
retries are the client library's, for network errors and rate limits.

## Clients are built when the LLM is set

The engine builds every agent's client when it is created, and again when the
LLM is replaced, without calling a model. Each engine keeps its clients in its
own cache (an LRU of 16), keyed on the agent's resolved settings: every
field of the agent, and the API key. The key and each header value enter the
cache key only as a SHA-256 digest, so a cache key can be logged and never
holds a secret. Agents with equal settings share one client: seven agents on one model
build one client.

A missing key does not stop the engine from starting:

- Building the engine (which `nl2sql index`, the API and the demo all do) does
  not require a chat provider key. A machine with no key can still run
  key-free work such as indexing with `embeddings: {provider: local}`. That
  holds for indexing because the `profiler` pass is genuinely
  optional: it is skipped, not merely deferred to a later failure.
- `engine.check()` reports every agent whose key does not resolve, without
  calling a model. Using that agent raises the same error, naming the agent,
  the provider and the environment variable to set:

  ```text
  ValueError: LLM agent 'default' uses provider 'openai', which requires an API
  key, but none could be resolved. Set OPENAI_API_KEY ... or in the environment,
  or give the agent an 'api_key' in the LLM config file.
  ```

The key is read when the clients are built. A key set in the environment after
the engine started is used only once the LLM is set again.

Genuine misconfiguration fails immediately: an unknown provider, or an agent
with no model.

## Replacing the LLM at runtime

`engine.llm.replace` swaps the whole `llm` section at once, while the engine
runs:

```python
from nl2sql import LLM, AgentLLM

change = engine.llm.replace(
    LLM(provider="anthropic", model="claude-sonnet-5-5",
        agents={"ast_planner": AgentLLM(model="claude-opus-5-5")}),
    by=user,          # optional: the UserContext making the change
)
print(change.changes)  # {"default": {"old": "openai/gpt-5.4", "new": "anthropic/claude-sonnet-5-5"}, ...}
```

- **Validated like the file.** The new section is checked as the `llm`
  section of `nl2sql.yaml` is: every provider must be registered and
  installed, and every agent's API key must resolve. If anything is wrong it
  raises `ConfigError` and nothing changes. No model is called.
- **Atomic.** The new clients are built first, then the engine's LLM and
  `engine.config.llm` are swapped together. A query reads the engine's LLM once,
  when it starts: queries already running finish on the old LLM, and every
  query that starts afterwards uses the new one. No query mixes the two.
- **Logged and audited.** One INFO line on the `nl2sql.llm` logger and one
  `llm.changed` audit record name who made the change (`by`'s user and roles
  when given, otherwise the entry point, `python` by default) and, for each
  agent whose provider or model changed, the old and the new one. They never
  hold a key, a key reference or a header.

  ```text
  INFO nl2sql.llm: LLM replaced by user=u42 roles=[admin] via api: default: openai/gpt-5.4 -> anthropic/claude-sonnet-5-5; ast_planner: openai/gpt-5.4 -> anthropic/claude-opus-5-5
  ```

- **Not saved.** Nothing is written to disk, so a restart goes back to the
  config file.
- **Cached clients.** Swapping back to an earlier LLM reuses the clients built
  for it. A client that falls out of the cache is closed (its `close()`, when
  it has one) once no running query uses it.
- **Plans follow the model.** The plan cache key includes the planner's
  provider and model, so after a swap a plan written by another model is never
  served. Swapping back hits the old plans again. See
  [Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

`replace` also takes the section as written in `nl2sql.yaml`, as a mapping,
checked by the same rules as the file. A `${...}` reference is kept in a secret
field (`api_key`, a `headers` value) and is an error anywhere else, so data that did not come
from your files never reads the environment. `nl2sql-api` serves it as the
admin-only [`PUT /api/v1/settings/llm`](../api/rest/llm.md): the body is the
section as JSON, and a bad one is HTTP 422 with the `ConfigError` text. The
playground's model switch calls `replace` too. `engine.llm.list()` shows the
model each agent runs on.

## Embeddings are a separate path

None of Anthropic, OpenRouter or Ollama covers embeddings — they are not routed through
`LLMRegistry` at all. Each engine builds its own embedder from its
[`embeddings` section](embeddings.md): `{provider: local}` embeds with the
key-free ONNX `all-MiniLM-L6-v2` model bundled with chromadb, and
`{provider: openai}` reads `OPENAI_API_KEY` (or `embeddings.api_key`).
Switching embedding providers requires a re-index.

## Prices

Every model call is priced, so `QueryResult.usage` reports a `cost`, from a
dated price table the engine ships (`nl2sql/llm/prices.py`, USD per million
tokens, each row with its source), with `prices` in the config adding a model
or replacing a row (a negotiated rate, a fine-tune):

```yaml
prices:
  gpt-4o: { input: 2.5, cached_input: 1.25, output: 10 }
```

| Model | Input | Cached input | Cache write | Output |
| --- | --- | --- | --- | --- |
| gpt-5.4 | 2.50 | 0.25 | 2.50 | 15.00 |
| gpt-5.4-mini | 0.75 | 0.075 | 0.75 | 4.50 |
| claude-opus-5 | 5.00 | 0.50 | 6.25 | 25.00 |
| claude-sonnet-5 | 2.00 | 0.20 | 2.50 | 10.00 |
| claude-haiku-4-5 | 1.00 | 0.10 | 1.25 | 5.00 |

The Claude rows match Anthropic's first-party prices, with the 5-minute
cache write (1.25x input; the engine never asks for the 1-hour cache, which
costs 2x). The OpenAI rows still need verifying against OpenAI's published
prices (owner to confirm); put a row in `llm.prices` if yours differ.

The table was checked on 2026-09-21; `engine.llm.prices()` returns it as the
engine uses it, `llm.prices` applied: `checked_on`, and per model the four
rates, `verified`, `source` (`table` or `config`) and `citation`.

- **Exact names, overrides first.** A call is priced from `llm.prices` by the
  model name the provider reports, then by the name configured, and only then
  from the table, by either name. So a negotiated rate for a deployment alias
  (`acme-gpt`) holds even when the provider reports a table model
  (`gpt-5.4`), and a dated snapshot the provider reports
  (`gpt-5.4-2026-03-05`) is priced by its configured name. Matches are exact:
  `gpt-5.4` never prices `gpt-5.4-mini`. A rate is never negative.
- **Unknown is null, never zero.** A model neither lists has a `null` cost,
  and so does any total that includes one of its calls. A token count the
  provider did not report (a cache read, reasoning tokens) is `null` too.
- **Each part at its own rate.** Cache reads at `cached_input`, cache writes
  at `cache_write`, the rest of the prompt at `input`, and the output,
  reasoning tokens included, at `output`. In a `prices` row, `cached_input`
  defaults to `input`, and `cache_write` to `input` times the wire's write
  premium: 1.25 for Claude (`provider: anthropic`, whose 5-minute cache write
  Anthropic bills at 1.25x input) and 1 for the OpenAI wire.
- **A cost cap needs prices.** With
  [`settings.pipeline.max_cost_usd`](settings.md#per-question-caps) set, every
  model the `llm` section names must have a price, or the engine does not
  build (a `ConfigError` naming each model, with the fix
  `add llm.prices.<model>`); `engine.llm.replace` refuses such a model the
  same way.

## Notes

- `agents` overrides give individual nodes, or the profiler's drafts, their own
  model and temperature; see [Per-node models](#per-node-models).
- `profiler` is optional and runs only when asked for. `nl2sql index` and
  `engine.index.build` call no model, so indexing works without it; what the
  profiler drafts is a proposal in the review queue and never replaces a comment
  read from the database or a description in the glossary.
- `nl2sql init --provider` takes `openai`, `anthropic` or `openrouter`, the
  providers with a default model, and writes no key. For `openai_compatible`
  or `ollama`, edit `llm.yaml` as shown above. `nl2sql demo --api-key`
  picks the provider from the key's shape (`sk-ant-` is Anthropic, `sk-or-` is
  OpenRouter, anything else is OpenAI).
