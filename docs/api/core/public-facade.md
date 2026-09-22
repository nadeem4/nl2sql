# Public Facade API

## Purpose
Provide a stable, consolidated entrypoint that wires `NL2SQLContext` and exposes
modular APIs through a single class.

## Responsibilities
- Initialize registries and stores in a consistent order.
- Expose convenience methods that delegate to modular APIs.
- Provide a single object for application integration.

## Key Modules
- `packages/nl2sql/src/nl2sql/public_api.py`
- `packages/nl2sql/src/nl2sql/context.py`

## Public Surface

### NL2SQL.__init__

Source:
`packages/nl2sql/src/nl2sql/public_api.py`

Signature:
`NL2SQL(ds_config_path: Optional[Union[str, pathlib.Path]] = None, secrets_config_path: Optional[Union[str, pathlib.Path]] = None, llm_config_path: Optional[Union[str, pathlib.Path]] = None, vector_store_path: Optional[Union[str, pathlib.Path]] = None, policies_config_path: Optional[Union[str, pathlib.Path]] = None, env: Optional[str] = None, env_file: Optional[Union[str, pathlib.Path]] = None)`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `ds_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Datasource config path override. Indexing reads each datasource's `description` from this file too. |
| `secrets_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Secrets config path override. |
| `llm_config_path` | `Optional[Union[str, pathlib.Path]]` | no | LLM config path override. |
| `vector_store_path` | `Optional[Union[str, pathlib.Path]]` | no | Vector store persistence path override. |
| `policies_config_path` | `Optional[Union[str, pathlib.Path]]` | no | Policies config path override. |
| `env` | `Optional[str]` | no | Environment name. Sets `ENV` and reloads settings, so `.env.{env}` is read from the working directory before any config path is resolved. |
| `env_file` | `Optional[Union[str, pathlib.Path]]` | no | Path to an env file. Sets `ENV_FILE_PATH` and reloads settings. Takes precedence over `env`. |

Returns:
`NL2SQL` instance with initialized context and API modules.

Raises:
- `FileNotFoundError`, `ValueError` from config loading (see `ConfigManager`).
- Provider-specific errors from secrets/LLM initialization.

Side Effects:
- Loads configs, resolves secrets, builds registries and stores.
- With `env` or `env_file`, writes `ENV` / `ENV_FILE_PATH` into the process
  environment and refreshes the `settings` singleton in place.

Example:

```python
from nl2sql import NL2SQL

engine = NL2SQL(env="demo")          # reads ./.env.demo
engine = NL2SQL(env_file="/etc/nl2sql/.env")
```

Idempotency:
- Initialization is not idempotent; it constructs new registries and stores.

### Convenience methods

The public facade delegates to modular APIs with the same signatures:
`run_query`, `add_datasource`, `add_datasource_from_config`, `list_datasources`,
`get_datasource_capabilities`, `configure_llm`, `configure_llm_from_config`,
`list_llms`, `get_llm`, `index_datasource`, `index_all_datasources`, `clear_index`,
`check_permissions`, `get_allowed_resources`, `get_current_settings`,
`get_setting`, `validate_configuration`.

### Top-level exports

Clients import everything they need from `nl2sql` itself, never from an engine
submodule: `NL2SQL`, `QueryResult`, `SubQueryResult`, `RowSample`,
`QuestionUsage`, `UserContext`, `configure_logging`, the error types
(`PipelineError`, `ErrorCode`, `ErrorSeverity`) and the modular API classes.
The REST API (`nl2sql-api`) is held to this rule by an architecture test.
