# Public Facade API

## Purpose
Provide one entry point: `NL2SQL` is built from one `Config`, owns everything
it builds (its `Runtime`), and is the only thing an application talks to.
Questions are top-level methods; everything else is a namespace.

## Responsibilities
- Build the engine from a `Config`, resolving every name through a `Registry`.
- Connect, and report what is ready, only when asked (`check()`).
- Release what it opened (`close()`).
- Answer questions (`run_query`, `stream_query`) and group the rest by subject
  (`engine.datasources`, `engine.index`, ...).

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
references only; `datasources.add`, `datasources.remove` and `llm.replace` are
the calls that change it.

Each engine owns its config: nothing is process-global and nothing writes
the environment, so two engines in one process share nothing. The CLI,
`nl2sql-api` and the playground build their engine from `nl2sql.yaml` and call
`check()` at startup.

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
| `check() -> CheckReport` | Connects to each datasource and records it `ready`, or `failed` with the stage (`connect`), the reason and a fix. A failed datasource is marked unavailable and left out of routing: the engine runs degraded, and a query that targets it fails with the reason. It also reports each agent whose API key does not resolve (no model is called) and the index status from `index.health()`. Raises `EngineNotReady` if a `required: true` datasource fails, or if datasources are configured and none is ready; the error carries the `report`. Safe to call again: a datasource that connects on a later call is routed to again. |
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

## The shape

```python
engine.run_query(question, *, datasource_id=None, execute=True, user_context=None, cancellation_token=None)
engine.stream_query(question, *, datasource_id=None, execute=True, user_context=None, cancellation_token=None)
engine.check(); engine.close(); engine.config; engine.about()

engine.datasources   # list, get, add, remove, schema, test
engine.index         # build, health, clear, inspect, inspect_defaults
engine.llm           # list, get, providers, replace
engine.policies      # roles, allows, allowed, validate
engine.curated       # validate, test
engine.results       # manifest, page, csv
engine.feedback      # record, list, stats, export, clear
engine.traces        # get, replay
engine.benchmark     # run_matrix, run_tier1, run_retrieval, tier2_configs, run_tier2
```

| namespace | reference |
| --- | --- |
| `run_query`, `stream_query`, `results` | [Query API](query.md) |
| `datasources` | [Datasource API](datasource.md) |
| `index` | [Indexing API](indexing.md) |
| `llm` | [LLM API](llm.md) |
| `policies` | [Policies API](policies.md) |
| `curated` | [Curated answers → Checking them](../../configuration/curated.md) |
| `feedback` | below, and [Feedback and signals](../../observability/feedback.md) |
| `traces`, `about()` | below |
| `benchmark` | [Benchmarks](../../benchmarks.md) |

The namespace classes are internal: reach them through the engine, never by
importing them. Each is built on first use.

The CLI, the playground and `nl2sql-api` are callers like any other: they use
only these methods and namespaces, never the runtime behind them.
`test_the_cli_the_playground_and_the_api_use_only_the_public_api` in
`tests/architecture/test_boundaries.py` holds them to that, and
`test_the_playground_the_demo_command_and_the_api_import_only_the_public_nl2sql`
holds the playground, `nl2sql demo` and `nl2sql-api` to importing from the
top-level `nl2sql` only (the playground and `nl2sql demo` keep a short, named
list of exceptions in that test, each with the reason and the change that
removes it).

### About

`engine.about()` describes the engine and its pipeline: `version` (of
`nl2sql-engine`), `git_sha` (when running from a checkout, else `None`),
`python`, and `steps`, one dict per step of a run in the order a run takes
them. Each step has its `node` (the name usage and timings are recorded
under), `label`, what it `does`, its `kind` (`model` when a model decides it,
`code` otherwise) and its `parent` (`sql_agent` for the steps nested in the SQL
agent). A model step also has its `agent` and the `provider` and `model` it is
configured to run on; never a key. The playground's Pipeline page reads it.

```python
about = engine.about()
[(s["node"], s["model"]) for s in about["steps"] if s["kind"] == "model"]
# [('curated_matcher', 'gpt-5.4'), ('datasource_resolver', 'gpt-5.4'), ...]
```

### Feedback

Ratings of answered runs, and the guardrail rates over recorded runs. They live
in the `feedback` table of `<storage.path>/state.db`; the rates also read the
traces in `<storage.path>/traces`. `nl2sql feedback` and the playground's
thumbs up and down are built on it. See
[Feedback and signals](../../observability/feedback.md) for what is stored.

| method | does |
| --- | --- |
| `record(result, *, question, role, rating, note=None) -> dict` | Rate one run, `"up"` or `"down"`, with an optional note (cut to 280 characters). `result` is the run's `QueryResult` or its JSON. The stored row is built from it and from the run's trace when one was kept; never rows, the answer, error messages or keys, and a run refused for the role keeps no SQL. A later rating of the same run replaces the earlier one. Returns the row; raises `ValueError` for another rating or a result with no `trace_id`. |
| `list(limit=None) -> list[dict]` | Every rating, newest first. |
| `stats(*, traces=None) -> dict` | Rates over the ratings plus the traces in `traces` (default: the engine's traces folder): feedback, refusals, refiner retries, validator failures, errors, and the plan cache's hit rate. |
| `export(path) -> dict` | Write the thumbs-up runs to `path` as draft gold entries for review; `{"written", "skipped"}`. Raises `ValueError` for the gold set's own path. |
| `clear() -> int` | Delete every rating; returns how many. |

```python
result = engine.run_query("How many customers are there?")
engine.feedback.record(result, question="How many customers are there?", role="admin", rating="up")
engine.feedback.stats()["feedback"]   # {'up': 1, 'down': 0, 'up_rate': 1.0, 'down_rate': 0.0}
```

### Traces

`engine.traces.get(trace_id)` returns one recorded run's trace document, or
`None` when none was kept (which runs are kept is `settings.traces.mode`). It
reads only `<storage.path>/traces`, and the id must be a plain token (letters,
digits, `-` and `_`), so no id can name a file elsewhere: anything else raises
`ValueError`, as does a file in another trace format.

`engine.traces.replay(trace)` re-runs a recorded question, answering every LLM
call from the trace document (no model is called), on a runtime of its own so
the engine's config and LLMs never change. It returns a `ReplayReport`: the
replayed `result`, how many recorded calls were `served`, the `divergence`
(where the replay asked for a call the recording does not have) and the
`differences` from the recorded result. It raises `LookupError` when a
datasource the trace used is not configured here or does not answer; the
message names each one and why. `nl2sql trace replay` is built on it (see
[Debugging](../../observability/debugging.md)).

```python
engine = NL2SQL.demo()
engine.datasources.schema("chinook")["tables"][0]["name"]   # 'Album'
engine.index.health()["status"]                              # 'ok'
engine.index.inspect("top customers", k=4)["picks"]
```

### Top-level exports

Clients import everything they need from `nl2sql` itself, never from an engine
submodule: `NL2SQL`, `QueryResult`, `SubQueryResult`, `RowSample`,
`AnswerSource`, `ResultSummary`, `ResultManifest`, `ResultPage`,
`ResultNotFound`, `MAX_RESULT_PAGE_SIZE`, `QuestionUsage`, `QueryEvent`,
`UserContext`, `CancellationToken`, `configure_logging`, what the namespaces
return (`DatasourceInfo`, `IndexProgress`, `LLMChange`), the config (`Config`,
`load_config`, `load_env_file`, `ConfigError`, `Registry` and the section
classes `Datasource`, `Limits`, `LLM`, `AgentLLM`, `Embeddings`, `Policies`,
`Role`, `Storage`, `StoreConfig`, `Secrets`, `Curated`, `Settings`), what `check()` reports
(`CheckReport`, `DatasourceStatus`, `EngineNotReady`), the error types
(`PipelineError`, `ErrorCode`, `ErrorSeverity`), and the benchmark's
`BenchmarkAPI` and `BenchmarkConfig`. The list is fixed by
`tests/unit/test_public_surface.py`: adding or removing a name is a semver
decision. The REST API (`nl2sql-api`) is held to importing only from `nl2sql`
by an architecture test.
