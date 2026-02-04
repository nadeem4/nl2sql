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
| `provider` | `str` | yes | Provider name (only `openai` supported). |
| `model` | `str` | yes | Model identifier. |
| `temperature` | `float` | no | Sampling temperature (default `0.0`). |
| `api_key` | `Optional[SecretStr]` | no | API key or secret reference. |
| `name` | `str` | no | Agent name (default `default`). |

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
- `ImportError` if provider dependency is missing (`langchain-openai`).

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
- Only provider supported in core registry is `openai` (enforced by `LLMRegistry`).
- `LLMRegistry.get_llm()` falls back to `default` if name is missing.
- Determinism: OpenAI LLM is initialized with `seed=42`.
