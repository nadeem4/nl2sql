# LLM API

## Purpose
`engine.llm`: the model each agent runs on, and replacing them at runtime.

## Responsibilities
- List each agent and the provider and model it runs on (`list`, `get`).
- Replace the engine's whole `llm` section at runtime (`replace`).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/llm_api.py` (`LLMAPI`, internal)
- `packages/nl2sql/src/nl2sql/llm/registry.py`
- `packages/nl2sql/src/nl2sql/llm/llm_set.py`
- `packages/nl2sql/src/nl2sql/llm/client_cache.py`
- `packages/nl2sql/src/nl2sql/llm/models.py`
- `packages/nl2sql/src/nl2sql/config/models.py` (`LLM`, `AgentLLM`: the `llm` section)

## Public Surface

### AgentConfig

One agent's resolved settings, as `LLMRegistry` builds a client from them.
`agents_from_config(llm)` (`llm/registry.py`) makes one per agent from the
config's `llm` section ([LLM configuration](../../configuration/llm.md)).

Source:
`packages/nl2sql/src/nl2sql/llm/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `provider` | `str` | yes | Provider name: `openai`, `anthropic`, `openai_compatible`, its presets `openrouter` and `ollama`, or one a plugin registers. |
| `model` | `str` | yes | Model identifier. |
| `temperature` | `Optional[float]` | no | Sampling temperature (default `0.0`). `None` (`null` in YAML) sends no temperature parameter, for models that accept only their default. |
| `api_key` | `Optional[SecretStr]` | no | API key or secret reference. Masked in `repr`, `str` and JSON dumps. `None` reads the provider's standard variable. |
| `base_url` | `Optional[str]` | no | Endpoint override; defaults to the provider preset (`https://openrouter.ai/api/v1` for `openrouter`, `http://localhost:11434/v1` for `ollama`). Required for `openai_compatible`. |
| `structured_output` | `Optional[str]` | no | `tool_calling`, `json_schema` or `json_mode`. `None` uses the provider's default. |
| `parse_retries` | `int` | no | Retries of a structured answer that does not parse (default `1`). |
| `max_tokens` | `Optional[int]` | no | `None` uses the provider's default (16,000 for `anthropic`, unset for the others). |
| `timeout_s` | `Optional[float]` | no | Per request. `None` uses the client library's. |
| `max_retries` | `Optional[int]` | no | Network and rate-limit retries, done by the client. `None` uses the client library's. |
| `headers` | `Dict[str, SecretStr]` | no | Sent with every request. A value may be a `${provider:key}` reference, resolved when the client is built. Values are masked in `repr`, `str` and JSON dumps, and `list()` shows only the header names. |
| `name` | `str` | no | Agent name (default `default`). |


### LLMFileConfig

A set of agents the benchmark compares, one config at a time:
`LLMFileConfig.from_llm(llm)` turns an `llm` section into it.

Source:
`packages/nl2sql/src/nl2sql/llm/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `default` | `AgentConfig` | yes | The top level of the `llm` section. |
| `agents` | `Dict[str, AgentConfig]` | no | The agents that set their own fields. |

### llm.list

Signature:
`list() -> dict`

Returns each agent by its name in the `llm` section, `default` first: its
`provider`, `model`, `temperature` and `inherited` (True when the agent sets
nothing of its own and runs on `default`). An agent given a model object shows
its class as `model` and no provider. Never a key.

```python
engine.llm.list()["ast_planner"]
# {'provider': 'openai', 'model': 'gpt-5.5', 'temperature': 0.0, 'inherited': False}
```

### llm.get

Signature:
`get(agent: str) -> dict`

One agent's entry from `list()`. Raises `ValueError` for a name that is not an
agent, listing the agents.

### llm.providers

Signature:
`providers() -> list[dict]`

Each LLM provider this engine can use: the built-ins whose client library is
installed, and any a plugin or `Registry.add_llm` registered. Each has `name`,
`label`, `key_variable` (the environment variable it reads its key from;
`None` for one that needs no key, such as `ollama`) and `key_present` (whether
that variable is set). Never the key.

```python
engine.llm.providers()[0]
# {'name': 'anthropic', 'label': 'Anthropic', 'key_variable': 'ANTHROPIC_API_KEY', 'key_present': False}
```

### llm.replace

Source:
`packages/nl2sql/src/nl2sql/api/llm_api.py` (`Runtime.replace_llm` in
`packages/nl2sql/src/nl2sql/runtime.py`)

Signature:
`replace(llm: LLM | Mapping, *, by: Optional[UserContext] = None, via: str = "python") -> LLMChange`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `llm` | `LLM` or a mapping | yes | The whole `llm` section: an `LLM`, or the section as written in `nl2sql.yaml` (a mapping, such as a request body). A mapping is checked exactly as the file is: strict keys with "did you mean", the model's validators, references kept in secret fields. A `${...}` reference outside a secret field is an error rather than read, since the data did not come from the operator's file. |
| `by` | `Optional[UserContext]` | no | Who is making the change; its `user_id` and `roles` are logged. |
| `via` | `str` | no | The entry point making the change (`python`, `api`, `playground`). |

Returns:
`LLMChange` (exported from `nl2sql`): `by` (`via`, and `user_id` and `roles`
when `by` was given) and `changes`, each agent whose effective provider or
model changed as `{"old": "provider/model", "new": "provider/model"}`. It holds
no key.

Raises:
- `ConfigError` for a section that does not validate, a provider that is not
  registered or not installed, or an agent whose API key does not resolve.
  Nothing changes. `nl2sql-api` answers it as HTTP 422 with the same text
  ([`PUT /api/v1/settings/llm`](../rest/llm.md)).

Side Effects:
- Builds the new clients (no model call), then swaps the engine's `LLMSet`
  and `engine.config.llm` together under the engine's lock. Runs already in
  flight finish on the old set.
- One INFO line on the `nl2sql.llm` logger and one `llm.changed` audit record.
  Nothing is written to disk.

To use the `llm` section of a file: `engine.llm.replace(load_config(path).llm)`.

See [LLM configuration → Replacing the LLM at runtime](../../configuration/llm.md#replacing-the-llm-at-runtime).

### Internals: `AgentConfig` and `LLMFileConfig`

`AgentConfig` (`llm/models.py`) is one agent's resolved settings, as
`LLMRegistry` builds a client from them; `agents_from_config(llm)`
(`llm/registry.py`) makes one per agent from the `llm` section. Its fields are
`provider`, `model`, `temperature` (`None` sends no temperature parameter),
`api_key` (masked in `repr`, `str` and JSON dumps; `None` reads the provider's
standard variable), `base_url`, `structured_output`, `parse_retries`, `max_tokens`, `timeout_s`,
`max_retries`, `headers` (values masked like the key) and `name`. `LLMFileConfig` is a set of agents
the benchmark compares, one config at a time (`LLMFileConfig.from_llm(llm)`).
Neither is part of the public API.

## Behavioral Contracts
- The built-in providers are `openai`, `anthropic` and `openai_compatible`,
  and `openrouter` and `ollama`, which are named presets of `openai_compatible`
  with no code path of their own. They are rows of `PROVIDER_PRESETS` in
  `llm/registry.py`, which stays the one table of key variables, and
  `LLM_PROVIDERS` in `llm/providers.py` makes one `LLMProvider` per row. Any
  other provider is a plugin: a class implementing `LLMProvider`, interface
  version 2, registered with `registry.add_llm` or the `nl2sql.llms` entry
  point group. An unregistered name raises `ValueError: Unsupported LLM
  provider`, naming the valid ones.
- `LLMProvider` version 2 has `interface_version`, `label`, `api_key_env`,
  `requires`, `install_hint`, `structured_output` (the method an agent that
  sets none is asked with) and `create(agent: ResolvedAgent)`. `ResolvedAgent`
  carries the agent's `name`, `model`, `temperature`, resolved `api_key`,
  `base_url`, `structured_output` (the agent's, else the provider's),
  `max_tokens`, `timeout_s`, `max_retries` and resolved `headers`; neither the
  key nor a header value is in its `repr`. A provider of another version is
  rejected when it is loaded.
- Each preset names a wire type (`ProviderPreset.wire`), and the client is
  built by that wire's adapter in `llm/wires/` (`WIRES[preset.wire].build_client`).
  The `Wire` protocol (`llm/wires/base.py`) is `build_client(model, temperature,
  **kwargs)`, `structured_output_method`, `mark_cache(payload)` and
  `read_usage(response)`. `wires.structured(llm, schema)` is how every node asks
  for structured output, with the agent's `structured_output` method, which the
  registry records on the client it builds (`tool_calling` is LangChain's
  `function_calling`); for `json_mode` it adds the schema to the prompt, since
  JSON mode alone does not carry it. An answer that does not parse is sent
  back with its validation errors, up to the agent's `parse_retries` times
  (recorded on the client too), and then `wires.StructuredOutputError(agent,
  schema, attempts, errors)` is raised, which `failures.provider_error()`
  reports as `MODEL_OUTPUT_INVALID`. `wires.streaming_structured(llm, schema)`
  is the same with the first answer streamed; a retry is not streamed. A model
  object given in the config uses its wire's `structured_output_method` and
  one retry. `wires.wire_of(llm)` picks the adapter
  from the client.
  See [LLM configuration → Providers and wire types](../../configuration/llm.md#providers-and-wire-types).
- `openai`, `openai_compatible`, `openrouter` and `ollama` are on the
  `openai` wire, served by `ConfiguredChatOpenAI` (`llm/wires/openai.py`).
  `openai` uses the client's own default endpoint. `openai_compatible` has no
  endpoint of its own: without a `base_url` its agent raises a `ValueError`
  asking for one. `openrouter` defaults to `https://openrouter.ai/api/v1` and
  `ollama` to `http://localhost:11434/v1`; a config-supplied `base_url`
  overrides either.
- `anthropic` is on the `anthropic` wire, served by `CachingChatAnthropic`
  (`llm/wires/anthropic_client.py`), a `langchain-anthropic` `ChatAnthropic`
  whose requests go through the adapter's `mark_cache` (a
  `cache_control: {"type": "ephemeral"}` breakpoint on the last system block),
  with `max_tokens=16000` unless the agent sets `max_tokens`, and whose tool calls go through the adapter's
  `repair_tool_calls` (arguments Claude nested under a single key the schema
  does not declare -- `input`, `query`, ... -- are unwrapped). It needs the `nl2sql-engine[anthropic]` extra;
  without it `get_llm()` raises a `ValueError` naming the extra. See
  [LLM configuration → Anthropic](../../configuration/llm.md#anthropic-claude).
- `openai`, `anthropic` and `openrouter` require an API key (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`). `openai_compatible` and `ollama`
  do not: without an `api_key` their preset supplies the placeholder key
  `ChatOpenAI` insists on. `openai_compatible` never reads `OPENAI_API_KEY`.
- `max_tokens`, `timeout_s`, `max_retries` and `headers` reach the client
  when set (`ChatOpenAI`'s and `ChatAnthropic`'s `max_tokens`, `timeout`,
  `max_retries` and `default_headers`); unset, the client's own default holds.
- The engine's LLM is one immutable `LLMSet` (each agent's settings and
  client), built when the engine is created and on every `replace`, without a
  model call. A run reads it once, when it starts (`LLMRegistry.begin_run`), so
  a run never mixes two sets.
- Clients come from the engine's `LLMClientCache`, an LRU of 16 keyed on the
  agent's resolved settings with the API key and each header value only as a
  SHA-256 digest. Agents
  with equal settings share one client, and swapping back reuses cached
  clients. A client that falls out of the cache is closed (its `close()`, when
  it has one) once no set in use holds it; `engine.close()` closes them all.
- A provider that needs a key and cannot resolve one does not stop the engine
  being built: `check()` reports it (`missing_keys()`), and `get_llm()` for that
  agent raises a `ValueError` naming the agent, the provider and the
  environment variable to set - not the raw
  `openai.OpenAIError: Missing credentials`.
- Ollama is supported at the transport level. The pipeline calls
  `with_structured_output` (including the recursive `PlanModel` in the AST
  planner), so results depend on the chosen local model's structured-output
  capability.
- `LLMRegistry.get_llm()` falls back to `default` if the name is missing, and
  raises `ValueError` naming the missing agent when no `default` is registered
  either.
- Embeddings are not routed through `LLMRegistry`: each engine builds one
  embedder from its `embeddings` section, through the registry's embeddings
  providers (`openai`, which needs an OpenAI key even when chat runs through
  OpenRouter, or `local`, the key-free ONNX model bundled with chromadb), or
  uses the `Embeddings(instance=...)` it is given. Two engines never share one.
- Determinism: every OpenAI-protocol client is built with `seed=42` (Anthropic
  has no seed parameter), and every client with the agent's configured
  `temperature`, or none when it is `null` (see
  [LLM configuration → Temperature](../../configuration/llm.md#temperature)).
- A provider's HTTP 400 rejecting `temperature` is re-raised as a `ValueError`
  naming the model and agent and telling you to set `temperature: null`.
- `LLMRegistry.register_llms` (internal) registers each agent under its key
  in the mapping, whatever its `name` field says.
