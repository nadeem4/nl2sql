# SDK Interface Reference

The SDK, `nl2sql-sdk` (imported as `nl2sql_sdk`), holds the interfaces an
extension implements. It depends on pydantic and the standard library only.

- Adapters: `nl2sql_sdk.protocols.DatasourceAdapterProtocol`, with
  `nl2sql_sdk.contracts.AdapterRequest` and `nl2sql_sdk.contracts.ResultFrame`
- Secret providers: `nl2sql_sdk.secrets.SecretProvider`

Everything is also exported from `nl2sql_sdk` itself.

## Interface: `DatasourceAdapterProtocol`

Adapters expose a capability-driven interface:

```python
from nl2sql_sdk.protocols import DatasourceAdapterProtocol
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.capabilities import DatasourceCapability
```

### Class attributes

Three class attributes let the engine check an adapter before it builds one.

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `interface_version` | `int` | The version of this interface the adapter was written for: `1`. The engine accepts exactly the version it was built for, and any other version is a load error. |
| `driver_modules` | `tuple[str, ...]` | The modules the adapter needs to connect, for example `("psycopg2",)`. The engine looks for each one with `importlib.util.find_spec` before it builds the adapter. |
| `install_hint` | `str` | What to install when a driver module is missing, for example `pip install "nl2sql-engine[postgres]"`. |

```python
class SnowflakeAdapter:
    interface_version = 1
    driver_modules = ("snowflake.connector",)
    install_hint = 'pip install "nl2sql-snowflake"'
```

The adapter module must import without its driver, so import the driver where
it is used and not at the top of the module. The built-in adapters declare:

| Adapter | `driver_modules` | Extra |
| :--- | :--- | :--- |
| `sqlite` | `("sqlite3",)` | none: `sqlite3` ships with Python |
| `postgres` | `("psycopg2",)` | `nl2sql-engine[postgres]` |
| `mysql` | `("pymysql",)` | `nl2sql-engine[mysql]` |
| `mssql` | `("pyodbc",)` | `nl2sql-engine[mssql]`, plus Microsoft's ODBC driver |
| `duckdb` | `("duckdb", "duckdb_engine")` | `nl2sql-engine[duckdb]` |

### Mandatory Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `datasource_id` | `str` | Unique identifier (e.g., "production_db"). |
| `datasource_engine_type` | `str` | Engine type string (e.g., `postgres`, `rest`). |
| `row_limit` | `int` | Safety breaker (limit returned rows). |
| `max_bytes` | `int` | Safety breaker (limit payload size). |

### Mandatory Methods

#### `capabilities() -> set[DatasourceCapability]`

Declares supported capabilities (e.g., `supports_sql`, `supports_rest`,
`supports_bound_parameters`).

`supports_bound_parameters` (`DatasourceCapability.SUPPORTS_BOUND_PARAMETERS`)
says the adapter binds `AdapterRequest.parameters` by name to the `:name`
placeholders in the SQL, and never pastes a value into the SQL text. The
engine sends parameters (for a [verified query](../configuration/curated.md))
only to an adapter that declares it. Every built-in SQLAlchemy adapter does.

#### `execute(request: AdapterRequest) -> ResultFrame`

Executes a plan-specific request and returns a normalized `ResultFrame`.

* **Args**: `AdapterRequest` with `plan_type`, `payload`, and `parameters` (values to bind; empty for generated SQL)
* **Returns**: `ResultFrame` with `columns`, `rows`, `row_count`, and error metadata

A query the database rejects is a result, not an exception: return
`ResultFrame(success=False, error=ResultError(...))`. Two fields of the
`ResultError` decide what the engine does with it:

| Field | Meaning |
| --- | --- |
| `retryable` | `True` when the query itself was wrong and a different query could succeed: bad syntax, an unknown table, column or function, a type mismatch. The engine reports `EXECUTION_FAILED` and sends the sub-query back to the planner, with `safe_message` as the feedback, while retries remain. `False` (the default) for a refusal the engine cannot plan its way out of: permission, a timeout, a lost connection, a read-only database. The engine reports `DB_EXECUTION_ERROR` and ends the sub-query. |
| `safe_message` | Shown to the planner and the user. Keep it to one short line: no SQL echo, no stack trace, no driver dump, and for a refusal nothing that names a host or a user. |

Only the adapter knows its driver, so the classification is the adapter's;
the engine names no database. SQLAlchemy adapters inherit it from
`BaseSQLAlchemyAdapter` (see [SQLAlchemy adapters](sqlalchemy.md)). An exception
that escapes `execute` is still caught, as a non-retryable `EXECUTOR_CRASH`.

#### `fetch_schema_snapshot()`

Required by every adapter; there is no capability flag gating it.

### Optional Methods (SQL adapters)

#### `dry_run(sql: str)`

Validates SQL without executing it (or safely rolling back).

#### `explain(sql: str)`

Returns the execution plan.

#### `cost_estimate(sql: str)`

Returns cost/row estimates. The pipeline does not currently call it, and there is no capability flag for it: `DatasourceCapability` carries only what something queries.

### Optional hook: `render_sql(expression)`

`nl2sql_sdk.protocols.SqlRenderingAdapterProtocol`. The engine builds
every query as a sqlglot expression tree and, when the adapter has
`render_sql`, passes it the finished tree and executes the text it returns.
Without the hook the engine renders `expression.sql(dialect=adapter.get_dialect())`,
so an adapter written before the hook keeps working. `BaseSQLAlchemyAdapter`
implements that same default.

Override it only for what sqlglot cannot express for your database. The
SQLite adapter, for example, rewrites `EXTRACT` and date truncation with
`STRFTIME` and `DATE(x, 'start of month')`. Postgres, MySQL, SQL Server and
DuckDB use the default.

`get_dialect()` must return a sqlglot dialect name (`postgres`, `tsql`,
`mysql`, `duckdb`, `sqlite`, ...), not the SQLAlchemy name.

### Optional hook: `execute_batches(request, batch_size)`

`nl2sql_sdk.protocols.StreamingAdapterProtocol`. When the adapter has
`execute_batches`, the engine reads the result through it, `RESULT_FETCH_BATCH_SIZE`
rows at a time (default 10,000), and writes each batch to the sub-query's
result artifact as it arrives, so a large result is never held whole in memory
(see [Artifact Store](../storage/artifact-store.md#batched-writes)). Without
the hook the engine calls `execute` and writes its one `ResultFrame`, so an
adapter written before the hook keeps working unchanged.

The contract:

- It is a generator (or returns an iterator) of `ResultFrame`.
- Every frame carries the result's `columns` and at most `batch_size` rows. An empty result is one frame with its columns and no rows.
- A failure, before the first row or between batches, is one frame with `success=False` and an `error`, classified exactly as `execute` would (`retryable`, `safe_message`), and it is the last frame. The engine discards whatever it had written.
- The request is the same `AdapterRequest` `execute` gets; refuse one you cannot run the same way.

`BaseSQLAlchemyAdapter` implements it with `fetchmany` on a result opened with
`stream_results`, a server-side cursor on drivers that have one (see
[SQLAlchemy adapters](sqlalchemy.md)).

The hook is an optional protocol rather than a required method, so an adapter
without it needs no change.

### Result types every adapter returns

These are part of the contract, so the same plan gives the same values on
every database:

| Plan operation | Result type | Example |
| :--- | :--- | :--- |
| `DATE_PART(unit, date)`, unit `year`, `quarter`, `month`, `day` | INTEGER | `2011` |
| `DATE_TRUNC(unit, date)`, same units | ISO date string `YYYY-MM-DD` | `'2011-04-01'` |

The engine already wraps a date part in `CAST(... AS INT)` and a truncation
in a `YYYY-MM-DD` format; an adapter that rewrites the inner nodes keeps
those wrappers.

---

## Interface: `SecretProvider`

A secret provider is where `${id:key}` references are read from. The secrets
config names one by `type` (see [Secrets](../configuration/secrets.md)).

```python
from typing import ClassVar, Optional, Self

from pydantic import BaseModel, ConfigDict, SecretStr


class AcmeVaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    token: SecretStr


class AcmeVaultSecretProvider:
    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = AcmeVaultConfig

    def __init__(self, client):
        self.client = client

    @classmethod
    def from_config(cls, cfg: AcmeVaultConfig) -> Self:
        try:
            import acme_vault  # the client library, imported only here
        except ImportError:
            raise ImportError("Missing dependency 'acme-vault'. Install 'nl2sql-acme-vault'.") from None
        return cls(acme_vault.Client(cfg.url, cfg.token.get_secret_value()))

    def get_secret(self, key: str) -> Optional[str]:
        return self.client.read(key)
```

| Member | Description |
| :--- | :--- |
| `interface_version` | `1`. The engine accepts exactly the version it was built for. |
| `config_model` | A pydantic model for the provider's entry in the secrets config, apart from `id` and `type`. Set `extra="forbid"`, so a misspelt key fails when the config loads, and hold credentials as `SecretStr`, so they never print. |
| `from_config(cfg)` | Builds the provider from a validated `config_model` instance, after the engine has resolved any `${env:...}` in it. Raise `ImportError` naming what to install when the client library is missing. |
| `get_secret(key)` | Returns the secret stored under `key`, or `None` when there is none. |

A provider reads its settings from its entry only, never from the
environment. The module that defines it must import without its client
library, so the engine can validate the entry before building anything.

---

## Interfaces: the stores

A store keeps one part of the engine's data: the vector `index`, the `state`
(schema snapshots, plan cache, feedback), the run `traces` or the `results`.
The storage config names one per slot by `type` (see
[Storage](../configuration/storage.md)). The four interfaces live in
`nl2sql_sdk.stores`, which imports only pydantic and the standard library.

Every store class has the same four members, whatever its slot:

| Member | Description |
| :--- | :--- |
| `interface_version` | `1`. The engine accepts exactly the version it was built for. |
| `slot` | `"index"`, `"state"`, `"traces"` or `"results"`: the sub-block it fills. The engine refuses a store in another slot. |
| `config_model` | A pydantic model for the store's sub-block apart from `type`. Set `extra="forbid"`, so a misspelt key is an error, and hold credentials as `SecretStr`. |
| `from_config(cfg, *, folder)` | Builds the store from a validated `config_model` instance, after the engine has resolved any `${provider:key}` in it. `folder` is `storage.path`. It should write nothing: `nl2sql config check` builds the stores too. |

The slots' methods:

| Interface | Methods |
| :--- | :--- |
| `IndexStore` | `open(namespace, dimension)`, `add(namespace, entries)`, `query(namespace, vector, k, where=None, with_vectors=False)`, `get(namespace, where, with_vectors=False)`, `delete(namespace, where)`, `pointers(namespace)`, `set_pointers(namespace, updates)` (atomic), `namespaces()`, `drop(namespace)`, `close()` |
| `StateStore` | `migrate()`, `put_snapshot(snapshot)`, `get_snapshot(datasource_id, schema_version)`, `keep_snapshots(datasource_id, keep)`, `get_plan`, `put_plan`, `clear_plans()`, `add_feedback(row)`, `list_feedback(limit=None)`, `clear_feedback()`, `lock(name, ttl_s)`, `close()` |
| `TraceStore` | `write(trace_id, document)`, `get(trace_id)`, `purge(older_than)` |
| `ResultStore` | `write(key, chunks)`, `open(key)`, `exists(key)`, `purge(older_than)`. Bytes only: the engine writes the Parquet. |

The models they exchange: `Entry` (`id`, `text`, `vector`, `metadata`), `Hit`
(an entry with its `distance`, and its `vector` when asked for), `Filter`
(`eq`, `ne` and `in`, all of which must hold) and `BuildPointer` (which build
of a datasource is live, and the `schema_version` it was built from). The
engine embeds; a store keeps the vectors it is handed.

A results store, sketched:

```python
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, ClassVar, Iterable, Self

from pydantic import BaseModel, ConfigDict


class BucketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str


class BucketResults:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "results"
    config_model: ClassVar[type[BaseModel]] = BucketConfig

    def __init__(self, client, bucket: str):
        self.client, self.bucket = client, bucket

    @classmethod
    def from_config(cls, cfg: BucketConfig, *, folder: Path) -> Self:
        import acme_blob  # the client library, imported only here
        return cls(acme_blob.Client(), cfg.bucket)

    def write(self, key: str, chunks: Iterable[bytes]) -> int:
        return self.client.put(self.bucket, key, b"".join(chunks))

    def open(self, key: str) -> BinaryIO:
        return self.client.open(self.bucket, key)

    def exists(self, key: str) -> bool:
        return self.client.exists(self.bucket, key)

    def purge(self, older_than: datetime) -> int:
        return self.client.delete_before(self.bucket, older_than)
```

Declare it in the `nl2sql.stores` entry point group
(`bucket = "acme_nl2sql.results:BucketResults"`), or register it in code with
`registry.add_store("bucket", BucketResults)`. Then `results: {type: bucket,
bucket: nl2sql-results}`.

!!! note "Built-ins only, for now"
    The engine moves onto these interfaces one slot at a time during 0.6
    (the index, then the state, then the traces and results). Until a slot's
    move lands, the engine refuses a plugin store in that slot.

---

## Compliance Testing

All adapters should pass the compliance test suite (schema introspection, type mapping,
error handling, and result contract validation).
