# LLM API

## Purpose
Configure LLM providers and expose LLM configurations by name.

## Responsibilities
- Register LLM configs programmatically or from file.
- Provide config lookup and listing.

## Key Modules
- `packages/core/src/nl2sql/api/llm_api.py`
- `packages/core/src/nl2sql/llm/registry.py`
- `packages/core/src/nl2sql/llm/models.py`
- `packages/core/src/nl2sql/configs/llm.py`

## Public Surface

### AgentConfig

Source:
`packages/core/src/nl2sql/llm/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `provider` | `str` | yes | Provider name: `openai` or `openrouter`. |
| `model` | `str` | yes | Model identifier. |
| `temperature` | `float` | no | Sampling temperature (default `0.0`). |
| `api_key` | `Optional[SecretStr]` | no | API key or secret reference. |
| `base_url` | `Optional[str]` | no | Endpoint override; defaults to the OpenRouter gateway when `provider` is `openrouter`. |
| `name` | `str` | no | Agent name (default `default`). |

!!! note
    `AgentConfig` is duplicated in `packages/core/src/nl2sql/configs/llm.py`
    (read/written by `ConfigManager` and `LLMGenerator`) and in
    `packages/core/src/nl2sql/llm/models.py` (consumed by `LLMRegistry`). The
    two must be kept in sync until they are unified.

### LLMFileConfig

Source:
`packages/core/src/nl2sql/configs/llm.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `version` | `int` | yes | Schema version (defaults to 1). |
| `default` | `AgentConfig` | yes | Default LLM configuration. |
| `agents` | `Dict[str, AgentConfig]` | no | Per-agent overrides. |

### LLM_API.configure_llm

Source:
`packages/core/src/nl2sql/api/llm_api.py`

Signature:
`configure_llm(config: Union[AgentConfig, Dict[str, Any]]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `AgentConfig | Dict[str, Any]` | yes | LLM config; `name` defaults to `default` if omitted. |

Returns:
`None`.

Raises:
- `ValueError` for unsupported provider.

Side Effects:
- Registers LLM config and instantiates provider client.

Idempotency:
- Re-registering same `name` overwrites config and client.

### LLM_API.configure_llm_from_config

Signature:
`configure_llm_from_config(config_path: Union[str, pathlib.Path]) -> None`

Raises:
- `FileNotFoundError` if config missing.
- `ValueError` for schema validation errors.

Side Effects:
- Registers all LLMs from file; also registers `default` from file.

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
- Providers supported in the core registry are `openai` and `openrouter`
  (enforced by `LLMRegistry`); anything else raises
  `ValueError: Unsupported LLM provider`.
- Both are served by `ChatOpenAI`. `openrouter` defaults to
  `https://openrouter.ai/api/v1`; a config-supplied `base_url` overrides that
  default and also lets the same path serve any other OpenAI-compatible
  endpoint (vLLM, LiteLLM, a local proxy).
- `LLMRegistry.get_llm()` falls back to `default` if the name is missing, and
  raises `ValueError` naming the missing agent when no `default` is registered
  either.
- Embeddings are not routed through the registry: `EmbeddingService` picks an
  embedder from `settings.embedding_provider` (`openai`, using
  `settings.openai_api_key`, or `local`, using the key-free ONNX model bundled
  with chromadb). With the default `openai` provider, `nl2sql index` needs an
  OpenAI key even when chat runs through OpenRouter. The embedder is cached per
  provider, so a runtime `reload_settings()` that changes the provider is
  honoured.
- Determinism: OpenAI LLM is initialized with `seed=42`.
