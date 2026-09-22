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

### Schema, index and retrieval

What a client shows about the index, as methods on `NL2SQL` itself. The
playground and `nl2sql-api` both use them, and neither reaches into
`engine.context`; `tests/unit/test_cli_demo_boundaries.py` holds the playground
to that.

| method | returns | meaning |
| --- | --- | --- |
| `get_schema(datasource_id)` | `dict` | The indexed schema as the planner is given it: `{"datasource_id", "tables": [...]}`, tables sorted by name, each with `name`, `schema`, `row_count`, `description`, `columns` (`name`, `type`, `nullable`, `primary_key`, `description`) and `foreign_keys` (`columns`, `references_table`, `references_columns`). Read from the latest schema snapshot, not the database; before the first index `tables` is empty. |
| `index_health()` | `dict` | `status` (`ok`, `empty`, `stale`, `missing`), `total`, `counts` by entry type, `built_at`, `embedding_model`, one entry per registered datasource (`entries`, `index_version`, `snapshot_version`, `built_at`) and `problems`. `missing` when no vector store is configured. |
| `rebuild_index(datasource_ids=None, enrich=False, full=False, on_progress=None, switch_guard=None)` | `RebuildResult` | Rebuilds the vector entries beside the live ones, then switches; the current entries answer questions until then. `enrich` asks the LLM for descriptions and spends tokens, so it is off by default. `on_progress` gets a sentence before each step; `switch_guard` is a context manager factory held around each switch. The result has `ok`, `stats`, `empty` and `errors`. |
| `inspect_retrieval(query, k=8, lambda_mult=None, types=None, datasource_id=None)` | `dict` | One MMR search of the live index, as the engine runs it: the pool with scores, the picks in order and what was dropped. `types` limits the entry types (`schema.table`, ...). Raises `LookupError` when no vector store is configured. |
| `reload_llm_config(config_path)` | `None` | Replaces every configured LLM with the file's, as one step: agents the file no longer names fall back to `default`. An invalid file leaves the current configuration in place. |

```python
engine = NL2SQL(env="demo")
engine.get_schema("chinook")["tables"][0]["name"]      # 'Album'
engine.index_health()["status"]                         # 'ok'
engine.inspect_retrieval("top customers", k=4)["picks"]
```

### Top-level exports

Clients import everything they need from `nl2sql` itself, never from an engine
submodule: `NL2SQL`, `QueryResult`, `SubQueryResult`, `RowSample`,
`QuestionUsage`, `UserContext`, `configure_logging`, the error types
(`PipelineError`, `ErrorCode`, `ErrorSeverity`) and the modular API classes.
The REST API (`nl2sql-api`) is held to this rule by an architecture test.
