# Public Facade API

## Purpose
Provide a stable, consolidated entrypoint that wires `NL2SQLContext` and exposes
modular APIs through a single class.

## Responsibilities
- Initialize registries and stores in a consistent order.
- Expose convenience methods that delegate to modular APIs.
- Provide a single object for application integration.

## Key Modules
- `packages/core/src/nl2sql/public_api.py`
- `packages/core/src/nl2sql/context.py`

## Public Surface

### NL2SQL.__init__

Source:
`packages/core/src/nl2sql/public_api.py`

Signature:
`NL2SQL(ds_config_path: Optional[Union[str, pathlib.Path]] = None, secrets_config_path: Optional[Union[str, pathlib.Path]] = None, llm_config_path: Optional[Union[str, pathlib.Path]] = None, vector_store_path: Optional[Union[str, pathlib.Path]] = None, policies_config_path: Optional[Union[str, pathlib.Path]] = None)`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `ds_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Datasource config path override. |
| `secrets_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Secrets config path override. |
| `llm_config_path` | `Optional[Union[str, pathlib.Path]]` | no | LLM config path override. |
| `vector_store_path` | `Optional[Union[str, pathlib.Path]]` | no | Vector store persistence path override. |
| `policies_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Policies config path override. |

Returns:
`NL2SQL` instance with initialized context and API modules.

Raises:
- `FileNotFoundError`, `ValueError` from config loading (see `ConfigManager`).
- Provider-specific errors from secrets/LLM initialization.

Side Effects:
- Loads configs, resolves secrets, builds registries and stores.

Idempotency:
- Initialization is not idempotent; it constructs new registries and stores.

### Convenience methods

The public facade delegates to modular APIs with the same signatures:
`run_query`, `add_datasource`, `add_datasource_from_config`, `list_datasources`,
`get_datasource_capabilities`, `configure_llm`, `configure_llm_from_config`,
`list_llms`, `get_llm`, `index_datasource`, `index_all_datasources`, `clear_index`,
`check_permissions`, `get_allowed_resources`, `get_current_settings`,
`get_setting`, `validate_configuration`.
