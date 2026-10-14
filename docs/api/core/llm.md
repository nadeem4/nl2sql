# LLM API

## Purpose
Configure LLM providers and expose LLM configurations by name.

## Responsibilities
- Replace the engine's whole LLM at runtime (`replace`).
- Register LLM configs programmatically, or replace them all with the `llm` section of an `nl2sql.yaml`.
- Provide config lookup and listing.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/llm_api.py`
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
| `provider` | `str` | yes | Provider name: `openai`, `anthropic`, `openrouter` or `ollama`. |
| `model` | `str` | yes | Model identifier. |
| `temperature` | `Optional[float]` | no | Sampling temperature (default `0.0`). `None` (`null` in YAML) sends no temperature parameter, for models that accept only their default. |
| `api_key` | `Optional[SecretStr]` | no | API key or secret reference. Masked in `repr`, `str` and JSON dumps. `None` reads the provider's standard variable. |
| `base_url` | `Optional[str]` | no | Endpoint override; defaults to the provider preset (`https://openrouter.ai/api/v1` for `openrouter`, `http://localhost:11434/v1` for `ollama`). |
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

### LLM_API.replace

Source:
`packages/nl2sql/src/nl2sql/api/llm_api.py` (`Runtime.replace_llm` in
`packages/nl2sql/src/nl2sql/runtime.py`)

Signature:
`replace(llm: LLM, *, by: Optional[UserContext] = None, via: str = "python") -> LLMChange`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `llm` | `LLM` | yes | The whole `llm` section, as in `nl2sql.yaml`. |
| `by` | `Optional[UserContext]` | no | Who is making the change; its `user_id` and `roles` are logged. |
| `via` | `str` | no | The entry point making the change (`python`, `api`, `playground`). |

Returns:
`LLMChange`: `by` (`via`, and `user_id` and `roles` when `by` was given) and
`changes`, each agent whose effective provider or model changed as
`{"old": "provider/model", "new": "provider/model"}`. It holds no key.

Raises:
- `ConfigError` for a provider that is not registered or not installed, or an
  agent whose API key does not resolve. Nothing changes.

Side Effects:
- Builds the new clients (no model call), then swaps the engine's `LLMSet`
  and `engine.config.llm` together under the engine's lock. Runs already in
  flight finish on the old set.
- One INFO line on the `nl2sql.llm` logger and one `llm.changed` audit record.
  Nothing is written to disk.

See [LLM configuration → Replacing the LLM at runtime](../../configuration/llm.md#replacing-the-llm-at-runtime).

### LLM_API.configure_llm

Source:
`packages/nl2sql/src/nl2sql/api/llm_api.py`

Signature:
`configure_llm(config: Union[AgentConfig, Dict[str, Any]]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `AgentConfig | Dict[str, Any]` | yes | LLM config; `name` defaults to `default` if omitted. |

Returns:
`None`.

Raises:
- `ValueError` for an unsupported provider or an empty model.

Side Effects:
- Sets that agent and keeps the others, as one swap of the engine's `LLMSet`.
  The client is built now, through the engine's client cache. `engine.config.llm`
  is not updated; `replace` is the way to change the whole section.

Idempotency:
- Re-registering same `name` overwrites the config; a client still used by
  another agent or a run in flight is kept.

### LLM_API.configure_llm_from_config

Signature:
`configure_llm_from_config(config_path: Union[str, pathlib.Path]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config_path` | `Union[str, pathlib.Path]` | yes | An `nl2sql.yaml`; its `llm` section is used. |

Raises:
- `ConfigError` if the file does not load, naming every problem.

Side Effects:
- Replaces every agent with the file's `llm` section, as one step; an agent
  the section no longer names falls back to the top level. An invalid file
  changes nothing. `NL2SQL.reload_llm_config(path)` does the same.

### LLM_API.get_llm

Signature:
`get_llm(name: str) -> dict`

Returns:
LLM configuration (API key excluded). Falls back to `default` if not found.

### LLM_API.list_llms

Signature:
`list_llms() -> dict`

Returns:
Map of LLM name → config (API key excluded).

## Behavioral Contracts
- Providers supported in the core registry are `openai`, `anthropic`,
  `openrouter` and `ollama`, held in `PROVIDER_PRESETS` in `llm/registry.py`;
  anything else raises `ValueError: Unsupported LLM provider`, naming the valid
  ones.
- Each preset names a wire type (`ProviderPreset.wire`), and the client is
  built by that wire's adapter in `llm/wires/` (`WIRES[preset.wire].build_client`).
  The `Wire` protocol (`llm/wires/base.py`) is `build_client(model, temperature,
  **kwargs)`, `structured_output_method`, `mark_cache(payload)` and
  `read_usage(response)`. `wires.structured(llm, schema)` is how every node asks
  for structured output; `wires.wire_of(llm)` picks the adapter from the client.
  See [LLM configuration → Providers and wire types](../../configuration/llm.md#providers-and-wire-types).
- `openai`, `openrouter` and `ollama` are on the `openai` wire, served by
  `ConfiguredChatOpenAI` (`llm/wires/openai.py`), and differ only by preset:
  `openai` uses the client's own default endpoint, `openrouter` defaults to
  `https://openrouter.ai/api/v1`, `ollama` to `http://localhost:11434/v1`. A
  config-supplied `base_url` overrides the preset, which also lets the same path
  serve any other OpenAI-compatible endpoint (vLLM, LiteLLM, a local proxy).
- `anthropic` is on the `anthropic` wire, served by `CachingChatAnthropic`
  (`llm/wires/anthropic_client.py`), a `langchain-anthropic` `ChatAnthropic`
  whose requests go through the adapter's `mark_cache` (a
  `cache_control: {"type": "ephemeral"}` breakpoint on the last system block),
  with `max_tokens=16000`, and whose tool calls go through the adapter's
  `repair_tool_calls` (arguments Claude nested under a single key the schema
  does not declare -- `input`, `query`, ... -- are unwrapped). It needs the `nl2sql-engine[anthropic]` extra;
  without it `get_llm()` raises a `ValueError` naming the extra. See
  [LLM configuration → Anthropic](../../configuration/llm.md#anthropic-claude).
- `openai`, `anthropic` and `openrouter` require an API key (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`); `ollama` does not, and its preset
  supplies the placeholder key `ChatOpenAI` insists on.
- The engine's LLM is one immutable `LLMSet` (each agent's settings and
  client), built when the engine is created and on every `replace`, without a
  model call. A run reads it once, when it starts (`LLMRegistry.begin_run`), so
  a run never mixes two sets.
- Clients come from the engine's `LLMClientCache`, an LRU of 16 keyed on the
  agent's resolved settings with the API key only as a SHA-256 digest. Agents
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
- `register_llms` registers each agent under its key in the mapping, whatever
  its `name` field says.
