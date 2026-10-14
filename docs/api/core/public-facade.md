# Public Facade API

## Purpose
Provide one entry point: `NL2SQL` is built from one `Config`, owns everything
it builds (its `Runtime`), and exposes the modular APIs through a single class.

## Responsibilities
- Build the engine from a `Config`, resolving every name through a `Registry`.
- Connect, and report what is ready, only when asked (`check()`).
- Release what it opened (`close()`).
- Expose convenience methods that delegate to modular APIs.

## Key Modules
- `packages/nl2sql/src/nl2sql/public_api.py`: the facade
- `packages/nl2sql/src/nl2sql/runtime.py`: `Runtime.from_config`, what one engine builds
- `packages/nl2sql/src/nl2sql/check.py`: `CheckReport`, `DatasourceStatus`, `EngineNotReady`

## Creating an engine

Every way builds the same `Config` (see
[Configuration v2, section 4](../../architecture/configuration_v2.md#4-creating-the-engine)).
Names in it (`type: postgres`, `provider: anthropic`) are resolved through a
`Registry`; `Registry.default()` is used when none is passed.

| constructor | meaning |
| --- | --- |
| `NL2SQL(config, *, registry=None)` | Loads and validates: resolves every name, imports only the components the config uses, checks interface versions and driver modules, builds the secret manager, the adapters (not connected), the LLM settings (no model call), the embedder and the stores under `storage.path`. Raises one `ConfigError` listing every problem. **It never connects** to a datasource or calls a model. |
| `NL2SQL.from_config(path=None, *, registry=None)` | `NL2SQL(load_config(path), registry=registry)`. `path` defaults to `$NL2SQL_CONFIG`, then `./nl2sql.yaml`. |
| `NL2SQL.demo(*, llm=None, path=".nl2sql-demo")` | The demo: the three bundled SQLite databases, opened read-only from the installed package, with the demo roles and verified queries. Without `llm`, the first provider whose key variable is set is used with its default model; with none set, a `ConfigError` names the variables. |

```python
from nl2sql import NL2SQL, Config, Datasource, Embeddings, LLM, Policies, Storage, load_config

engine = NL2SQL.from_config("nl2sql.yaml")

engine = NL2SQL(Config(
    datasources=[Datasource(id="shop", type="sqlite", connection={"database": "shop.db"})],
    llm=LLM(provider="anthropic", model="claude-sonnet-5-5"),
    embeddings=Embeddings(provider="local"),
    policies=Policies.allow_all(),
    storage=Storage(path=".nl2sql"),
))

cfg = load_config("nl2sql.yaml").with_datasource(
    Datasource(id="hr", type="sqlite", connection={"database": "hr.db"}))
engine = NL2SQL(cfg)
```

What one engine builds belongs to it alone: its secret manager (resolving
`${provider:key}` references through the providers in `secrets`), its
embedder (from `embeddings`, or the `instance` given), and its stores:

| store | location |
| --- | --- |
| Vector index | `<storage.path>/index/` |
| Schema snapshots, plan cache, feedback | `<storage.path>/state.db` |
| Run traces | `<storage.path>/traces/` |
| Result files | `<storage.path>/results/` (see [Artifact Store](../../storage/artifact-store.md)) |

A relative `storage.path`, and a relative SQLite or DuckDB file in a
datasource's `connection`, are read from the folder of `nl2sql.yaml` when the
config was loaded from a file, and from the working directory when it was
built in code. `engine.config` is the current frozen `Config`, with secrets as
references only; `add_datasource` is the one call that changes it.

!!! note "Until the settings global is gone"
    Modules not yet moved onto the runtime still read the process-global
    `settings`, and building an engine copies its config into it. Two engines
    in one process therefore share those values until the last migration pull
    request (configuration v2, section 8). The CLI, `nl2sql-api` and the
    playground build their engine from `nl2sql.yaml` and call `check()` at
    startup.

## Lifecycle

```python
with NL2SQL.from_config("nl2sql.yaml") as engine:
    report = engine.check()
    if not report.ok:
        print(report)
    result = engine.run_query("Top 5 customers by revenue")
# close() ran: pools disposed, stores closed
```

| method | meaning |
| --- | --- |
| `check() -> CheckReport` | Connects to each datasource and records it `ready`, or `failed` with the stage (`connect`), the reason and a fix. A failed datasource is marked unavailable and left out of routing: the engine runs degraded, and a query that targets it fails with the reason. It also reports each agent whose API key does not resolve (no model is called) and the index status from `index_health()`. Raises `EngineNotReady` if a `required: true` datasource fails, or if datasources are configured and none is ready; the error carries the `report`. Safe to call again: a datasource that connects on a later call is routed to again. |
| `close()` | Disposes each adapter's connection pool (an adapter's optional `close()`) and closes the state store and the vector store client. `run_query`, `stream_query` and `check` raise afterwards. Calling it twice is fine. |
| `with NL2SQL(...) as engine:` | `__exit__` calls `close()`. |
| `config` | The current frozen `Config`. |
| `closed` | Whether `close()` ran. |

`CheckReport` has `datasources` (one `DatasourceStatus` each: `id`, `status`,
`required`, `stage`, `reason`, `fix`), `llm` (agent to reason, for each key
that does not resolve), `index`, and `ok`, true when every datasource is
ready, every key resolves and the index is built. `str(report)` is a readable
summary.

Building an engine is cheap and has no side effects outside `storage.path`, so
it suits tests and notebooks. Startup policy belongs to the entry point: call
`check()` straight after building the engine, refuse to start on
`EngineNotReady`, and start degraded, printing the report, when an optional
datasource fails.

### Convenience methods

The public facade delegates to modular APIs with the same signatures:
`run_query`, `add_datasource` (a `Datasource`: built, not connected, and added to `engine.config`), `add_datasource_from_config`, `list_datasources`,
`get_datasource_capabilities`, `configure_llm`, `configure_llm_from_config`,
`list_llms`, `get_llm`, `index_datasource`, `index_all_datasources`, `clear_index`,
`check_permissions`, `get_allowed_resources`, `get_current_settings`,
`get_setting`, `validate_configuration`, `stream_query` (the run as events: see
[Query API → Streaming a run](query.md#streaming-a-run)), and the saved-result reads
`get_result_manifest`, `get_result_page` and `export_result_csv` (see
[Query API → Reading a run's saved results](query.md#reading-a-runs-saved-results)),
which `nl2sql-api` and the playground both serve.

### Schema, index and retrieval

What a client shows about the index, as methods on `NL2SQL` itself. The
playground and `nl2sql-api` both use them, and neither reaches into
`engine.context`; `tests/unit/test_cli_demo_boundaries.py` holds the playground
to that.

| method | returns | meaning |
| --- | --- | --- |
| `get_schema(datasource_id)` | `dict` | The indexed schema as the planner is given it: `{"datasource_id", "tables": [...]}`, tables sorted by name, each with `name`, `schema`, `row_count`, `description`, `columns` (`name`, `type`, `nullable`, `primary_key`, `description`) and `foreign_keys` (`columns`, `references_table`, `references_columns`). Read from the latest schema snapshot, not the database; before the first index `tables` is empty. |
| `index_health()` | `dict` | `status` (`ok`, `empty`, `stale`, `missing`), `total`, `counts` by entry type, `built_at`, `embedding_model`, one entry per registered datasource (`entries`, `index_version`, `snapshot_version`, `built_at`), `problems` and `warnings`. `missing` when no vector store is configured. A datasource that failed to start is a warning: "Datasource 'x' is unavailable: <reason>". |
| `list_unavailable_datasources()` | `dict` | Configured datasources that `check()` could not connect to, mapped to the reason. They are not in `list_datasources()` until a later `check()` connects. See [Datasources → When a datasource fails to start](../../configuration/datasources.md#when-a-datasource-fails-to-start). |
| `rebuild_index(datasource_ids=None, enrich=False, full=False, on_progress=None, switch_guard=None)` | `RebuildResult` | Rebuilds the vector entries beside the live ones, then switches; the current entries answer questions until then. `enrich` asks the LLM for descriptions and spends tokens, so it is off by default. `on_progress` gets a sentence before each step; `switch_guard` is a context manager factory held around each switch. The result has `ok`, `stats`, `empty` and `errors`. |
| `inspect_retrieval(query, k=8, lambda_mult=None, types=None, datasource_id=None)` | `dict` | One MMR search of the live index, as the engine runs it: the pool with scores, the picks in order and what was dropped. `types` limits the entry types (`schema.table`, ...). Raises `LookupError` when no vector store is configured. |
| `reload_llm_config(config_path)` | `None` | Replaces every configured LLM with the `llm` section of the `nl2sql.yaml` at `config_path`, as one step: agents the section no longer names fall back to the top level. An invalid file leaves the current configuration in place. |

```python
engine = NL2SQL.demo()
engine.get_schema("chinook")["tables"][0]["name"]      # 'Album'
engine.index_health()["status"]                         # 'ok'
engine.inspect_retrieval("top customers", k=4)["picks"]
```

### Top-level exports

Clients import everything they need from `nl2sql` itself, never from an engine
submodule: `NL2SQL`, `QueryResult`, `SubQueryResult`, `RowSample`,
`ResultSummary`, `ResultManifest`, `ResultPage`, `ResultNotFound`,
`MAX_RESULT_PAGE_SIZE`, `QuestionUsage`, `QueryEvent`, `UserContext`, `CancellationToken`,
`configure_logging`, the config (`Config`, `load_config`, `ConfigError`, `Registry` and the section classes `Datasource`, `Limits`, `LLM`, `AgentLLM`, `Embeddings`, `Policies`, `Role`, `Storage`, `Secrets`, `Curated`, `Settings`), what `check()` reports (`CheckReport`, `DatasourceStatus`, `EngineNotReady`), the error types
(`PipelineError`, `ErrorCode`, `ErrorSeverity`) and the modular API classes.
The REST API (`nl2sql-api`) is held to this rule by an architecture test.
