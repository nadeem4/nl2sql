# SDK Interface Reference

The SDK, `nl2sql-sdk` (imported as `nl2sql_sdk`), holds the interfaces an
extension implements. Its core depends on pydantic and the standard library only.

- Adapters: `nl2sql_sdk.DatasourceAdapter` (interface version 2), with the
  models it is built from (`ConnectionModel`, `Limits`, `IndexScope`) and
  returns (`ConnectionCheck`, `SchemaSnapshot`, `ResultFrame`)
- The SQLAlchemy base: `nl2sql_sdk.sqlalchemy.SQLAlchemyAdapter` and
  `SQLAlchemyConnection`, installed with `pip install "nl2sql-sdk[sqlalchemy]"`.
  The one part of the SDK that imports SQLAlchemy; see
  [SQLAlchemy adapters](sqlalchemy.md).
- The conformance kit: `nl2sql_sdk.testing.AdapterConformance`, installed
  with `pip install "nl2sql-sdk[testing]"`, a pytest test class every adapter
  should pass; see [the conformance kit](#the-conformance-kit-nl2sql_sdktesting).
- Secret providers: `nl2sql_sdk.secrets.SecretProvider`
- Stores: the four protocols in `nl2sql_sdk.stores`
- Policy checks: `nl2sql_sdk.policy.PolicyCheck`, with `UserContext` and
  `Grant`
- Knowledge sources: `nl2sql_sdk.knowledge.KnowledgeSource`, with the
  glossary's models; see [below](#interface-knowledgesource)

The adapter's interfaces and models are exported from `nl2sql_sdk` itself;
the stores, policy checks and knowledge sources are read from their modules.

## Interface: `DatasourceAdapter`

```python
from nl2sql_sdk import (AdapterRequest, ConnectionCheck, ConnectionModel, DatasourceAdapter,
                        DatasourceCapability, IndexScope, Limits, ResultFrame, SchemaSnapshot)
```

The contract, version 2:

```python
class DatasourceAdapter(Protocol):
    interface_version: ClassVar[int]   # 2
    title: ClassVar[str]               # "PostgreSQL", for people and forms
    connection_model: ClassVar[type[ConnectionModel]]
    driver_modules: ClassVar[tuple[str, ...]]
    install_hint: ClassVar[str]

    def __init__(self, datasource_id: str, connection: ConnectionModel, limits: Limits,
                 *, folder: Path | None = None) -> None: ...      # never connects
    def capabilities(self) -> set[DatasourceCapability]: ...
    def get_dialect(self) -> str: ...                  # a sqlglot dialect name
    def default_schema(self) -> str | None: ...
    def test_connection(self) -> ConnectionCheck: ...
    def fetch_schema_snapshot(self, scope: IndexScope,
                              on_table: Callable[[str], None] | None = None) -> SchemaSnapshot: ...
    def execute(self, request: AdapterRequest) -> ResultFrame: ...
    def close(self) -> None: ...
```

### Class attributes

The class attributes let the engine check an adapter, and show it to people,
before it builds one.

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `interface_version` | `int` | The version of this interface the adapter was written for: `2`. The engine accepts exactly the version it was built for, and any other version is a load error. |
| `title` | `str` | The database's name for people, such as `"PostgreSQL"`. |
| `connection_model` | `type[ConnectionModel]` | The model the datasource's `connection` block is validated against. See [The connection model](#the-connection-model). |
| `driver_modules` | `tuple[str, ...]` | The modules the adapter needs to connect, for example `("psycopg2",)`. The engine looks for each one with `importlib.util.find_spec` before it builds the adapter. |
| `install_hint` | `str` | What to install when a driver module is missing, for example `pip install "nl2sql-engine[postgres]"`. |

The adapter module must import without its driver, so import the driver where
it is used and not at the top of the module. The built-in adapters declare:

| Adapter | `title` | `connection_model` | `driver_modules` | Extra |
| :--- | :--- | :--- | :--- | :--- |
| `sqlite` | SQLite | `SqliteConnection` | `("sqlite3",)` | none: `sqlite3` ships with Python |
| `postgres` | PostgreSQL | `PostgresConnection` | `("psycopg2",)` | `nl2sql-engine[postgres]` |
| `mysql` | MySQL | `MysqlConnection` | `("pymysql", "cryptography")` | `nl2sql-engine[mysql]` |
| `mssql` | SQL Server | `MssqlConnection` | `("pyodbc",)` | `nl2sql-engine[mssql]`, plus Microsoft's ODBC driver |
| `duckdb` | DuckDB | `DuckdbConnection` | `("duckdb", "duckdb_engine")` | `nl2sql-engine[duckdb]` |

Each connection model is public, next to its adapter
(`from nl2sql.adapters.postgres.adapter import PostgresConnection`).

### The connection model

`ConnectionModel` is a pydantic model with `extra="forbid"` and
`hide_input_in_errors=True`. Subclass it with one field per setting:

```python
from typing import Literal

from pydantic import Field, SecretStr

from nl2sql_sdk import ConnectionModel


class PostgresConnection(ConnectionModel):
    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")
    sslmode: Literal["disable", "prefer", "require", "verify-full"] = Field(
        "prefer", title="SSL mode", json_schema_extra={"group": "advanced"})
```

- **Strict.** An unknown key is an error that names the field it was probably
  meant to be: `connection.hostname: unknown key 'hostname'. Did you mean
  'host'?`. No error message holds a value from the connection.
- **Validated when the adapter is built.** The engine resolves every
  `${provider:key}` reference in the `connection` block, validates it against
  `connection_model`, and passes the instance to the constructor. From Python,
  `Datasource(connection=PostgresConnection(...))` passes an instance, which
  is used as given; another adapter's model is refused.
- **Secrets.** Declare a credential as `SecretStr`: it never prints, and its
  JSON Schema is `"format": "password", "writeOnly": true`.
- **The form.** `connection_model.model_json_schema()` describes the form for
  connecting a database: `Field(title, description, examples)` label it, and
  `json_schema_extra={"group": "advanced"}` marks a field to fold away.

### The constructor

`adapter_cls(datasource_id, connection, limits, *, folder=None)`:

| Argument | What it is |
| :--- | :--- |
| `datasource_id` | The datasource's id. Keep it as `self.datasource_id`. |
| `connection` | The validated `connection_model` instance. |
| `limits` | The datasource's `Limits`: `row_limit`, `timeout_ms`, `max_bytes`, each `None` when unset. Enforce `timeout_ms` the way your driver allows. The engine caps a query's rows itself, from the datasource's `limits`. |
| `folder` | The folder of the `nl2sql.yaml` the datasource came from, or `None` when the config was built in code. Read a relative file path from it. |

The constructor must not connect: the adapter connects on first use, and
`engine.check()` connects through `test_connection()`.

### Methods

#### `capabilities() -> set[DatasourceCapability]`

Declares what the adapter does. Declare a flag only when it holds on a real
server.

| Flag | Meaning |
| :--- | :--- |
| `SUPPORTS_SQL` | The adapter runs SQL requests (`plan_type="sql"`). The SQL agent serves only a datasource that declares it. |
| `SUPPORTS_BOUND_PARAMETERS` | The adapter binds `AdapterRequest.parameters` by name to the `:name` placeholders in the SQL, and never pastes a value into the SQL text. The engine sends parameters (for a [verified query](../configuration/curated.md)) only to an adapter that declares it. Every built-in SQLAlchemy adapter does. |
| `ENFORCES_READ_ONLY` | The session cannot write: an `INSERT` or `CREATE TABLE` through `execute` fails. |
| `ENFORCES_TIMEOUT` | A statement running past `limits.timeout_ms` is stopped by the server or the driver. |
| `ESTIMATES_COST` | The adapter has `estimate(sql, parameters)` ([below](#optional-hooks-estimate-and-probe_overlap)): the database's estimate of the rows a query reads, without running it. Release 0.8's cost rule reads it. |

#### `get_dialect() -> str`

The **sqlglot** dialect name the engine renders SQL in: `postgres`, `tsql`,
`mysql`, `duckdb`, `sqlite`, or any other name `sqlglot.Dialect.get_or_raise`
accepts. It is not the SQLAlchemy dialect name (`postgresql`, `mssql`).

#### `default_schema() -> str | None`

The schema an unqualified table name resolves to, or `None` when unknown.
Policies read a plan table with no schema as a table in this schema; without
one, `main` is assumed.

#### `test_connection() -> ConnectionCheck`

Connects and reports what it found. It returns a failure rather than raising
it:

| Field | Meaning |
| :--- | :--- |
| `ok` | Whether the adapter connected. |
| `stage` | Which stage failed: `config`, `driver`, `network`, `auth`, `database` or `permission`; `None` when the adapter cannot tell. |
| `message` | Why, in one line, with no secret in it. |
| `fix` | What to do about it, when the adapter knows. |
| `server_version` | The server's version, when connected. |

`engine.check()` calls it for each datasource and reports the stage, message
and fix in the datasource's status.

#### `fetch_schema_snapshot(scope, on_table=None) -> SchemaSnapshot`

The schema of the tables and views the datasource's `index` scope wants, with
their metadata. `scope` is an `IndexScope` (see
[`index`](../configuration/datasources.md#index)): call
`scope.wants_table(ref)` and `scope.wants_column(ref, column)` rather than
matching its patterns yourself, and leave out your database's system schemas.
`on_table` is called with each table's `schema.table` as it is read, for
progress.

The snapshot's `issues` list holds an `IndexIssue` (`table`, `column`,
`stage`, `message`, and `error_type`, the exception's class name when an
exception caused it) for each table or column indexed without something, and
each table's `row_count_source` says where its `row_count` came from:
`catalog` (an estimate) or `exact` (a count).

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
`SQLAlchemyAdapter` (see [SQLAlchemy adapters](sqlalchemy.md)). An exception
that escapes `execute` is still caught, as a non-retryable `EXECUTOR_CRASH`.

#### `close()`

Releases the adapter's connections. The engine calls it once, from
`engine.close()`, and when a datasource is removed.

### Optional hook: `render_sql(expression)`

`nl2sql_sdk.protocols.SqlRenderingAdapterProtocol`. The engine builds
every query as a sqlglot expression tree and, when the adapter has
`render_sql`, passes it the finished tree and executes the text it returns.
Without the hook the engine renders `expression.sql(dialect=adapter.get_dialect())`,
so an adapter written before the hook keeps working. `SQLAlchemyAdapter`
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

`SQLAlchemyAdapter` implements it with `fetchmany` on a result opened with
`stream_results`, a server-side cursor on drivers that have one (see
[SQLAlchemy adapters](sqlalchemy.md)).

The hook is an optional protocol rather than a required method, so an adapter
without it needs no change.

### Optional hooks: `estimate` and `probe_overlap`

Two optional protocols in `nl2sql_sdk.protocols`, added for release 0.8
([Wave B](../architecture/wave_b.md), D45 and D52), found with `isinstance`
as the two hooks above are. The adapter interface stays at version 2.

| Protocol | Method | Contract |
| :--- | :--- | :--- |
| `EstimatingAdapterProtocol` | `estimate(sql, parameters) -> Estimate` | The database's estimate of the rows `sql` *reads*, without executing it, under `timeout_ms`; raise on failure. Declare `ESTIMATES_COST` with it. |
| `ProfilingAdapterProtocol` | `probe_overlap(left, right, sample_rows) -> Overlap` | Of up to `sample_rows` distinct non-null values of the column `left` (a `ColumnRef`), how many exist in `right`. Read-only, within the adapter's limits. |

`Estimate` (`nl2sql_sdk.models`) has `rows` (the rows the query is expected
to read, added up over the plan's scan operators: a full scan counts its
table, not its filtered output), `cost` (the planner's own units, for
display only) and `source` (what was read, for people). `Overlap` has
`sampled` and `matched`.

On the built-ins:

- **`probe_overlap`** is on the SDK's `SQLAlchemyAdapter`, so all five
  built-ins and every adapter on the base have it. It is one statement in
  SQLAlchemy Core (a `DISTINCT` sample of `left` with a limit, and an
  `EXISTS` on `right`), run on the adapter's own connections, so read-only
  and under `timeout_ms` wherever the adapter enforces them. The limit caps
  the sample, not the read: `DISTINCT` can read all of `left`, so on a
  large table the timeout is what bounds it. A failure raises
  `RuntimeError` naming the two columns and the driver's error class, never
  the driver's message, which can quote a value.
- **`estimate`** is on PostgreSQL, MySQL, SQL Server and DuckDB, which
  declare `ESTIMATES_COST`. SQLite does not: `EXPLAIN QUERY PLAN` has no row
  estimates. Each reads the database's plan without running the query and
  adds up what its scan operators read, per execution where the plan
  gives the executions; the four adapter pages say how and what they do not
  count. Every failure raises `RuntimeError`, which the cost rule reads as
  "no estimate".

### Result types every adapter returns

These are part of the contract, so the same plan gives the same values on
every database:

| Plan operation | Result type | Example |
| :--- | :--- | :--- |
| `DATE_PART(unit, date)`, unit `year`, `quarter`, `month`, `day` | INTEGER | `2011` |
| `DATE_TRUNC(unit, date)`, same units | ISO date string `YYYY-MM-DD` | `'2011-04-01'` |
| `DATE_DIFF('day', start, end)` | number, elapsed days with the fraction kept (negative when end is before start, NULL if either is) | `3.87` |

The engine builds a day difference as `DateDiff(end, start, SECOND) / 86400.0`; the base class's `render_sql` fixes it for MySQL and SQL Server, and an adapter whose database sqlglot renders it wrongly for (SQLite) rewrites the `DateDiff` node into a fractional seconds count.

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
| `interface_version` | `4`. The engine accepts exactly the version it was built for. Version 2 added `StateStore.put_schema_diff` and `get_schema_diff` (schema drift); version 3 the review queue's proposal methods and `BuildPointer.knowledge_hash`; version 4 the conversation, workspace, dashboard, stored chart result and API key methods. The version is one per kind, so a store of any slot declares `4`. |
| `slot` | `"index"`, `"state"`, `"traces"` or `"results"`: the sub-block it fills. The engine refuses a store in another slot. |
| `config_model` | A pydantic model for the store's sub-block apart from `type`. Set `extra="forbid"`, so a misspelt key is an error, and hold credentials as `SecretStr`. |
| `from_config(cfg, *, folder)` | Builds the store from a validated `config_model` instance, after the engine has resolved any `${provider:key}` in it. `folder` is `storage.path`. It should write nothing: `nl2sql config check` builds the stores too. |

The slots' methods:

| Interface | Methods |
| :--- | :--- |
| `IndexStore` | `open(namespace, dimension)`, `add(namespace, entries)`, `query(namespace, vector, k, where=None, with_vectors=False)`, `get(namespace, where, with_vectors=False)`, `delete(namespace, where)`, `pointers(namespace)`, `set_pointers(namespace, updates)` (atomic), `namespaces()`, `drop(namespace)`, `close()` |
| `StateStore` | `migrate()`, `put_snapshot(snapshot)`, `get_snapshot(datasource_id, schema_version)`, `list_versions(datasource_id)` (newest first), `keep_snapshots(datasource_id, keep)` (also deletes the diffs of the snapshots it deletes), `put_schema_diff(diff)` (keyed by `diff.datasource_id` and `diff.to_version`, which it requires; replaces one stored before), `get_schema_diff(datasource_id, schema_version)` (or `None`), `get_plan`, `put_plan`, `clear_plans()`, `add_feedback(row)`, `list_feedback(limit=None)`, `clear_feedback()`, `put_proposal(proposal)` (replaces the row with its id), `get_proposal(id)`, `list_proposals(kind=None, status=None, datasource_id=None, limit=None)` (newest first), `decide_proposal(id, status, *, by, reason=None, wrote=None, at=None)`, `purge_proposals(older_than)` (decided rows only), the version 4 methods on JSON documents (`dict`; see [The state store](../schema/store.md#conversations-dashboards-and-api-keys)): `put_conversation`, `get_conversation`, `list_conversations(tenant_id, user_id, limit)`, `delete_conversation`, `purge_conversations(older_than)`, `put_dashboard`, `get_dashboard`, `list_dashboards(tenant_id, user_id)`, `delete_dashboard`, `purge_dashboards(drafts_older_than)`, `list_scheduled_dashboards()`, `put_workspace`, `get_workspace`, `list_workspaces(tenant_id, user_id)`, `delete_workspace`, `put_tile_runs(dashboard_id, docs)`, `get_tile_runs(dashboard_id)`, `delete_tile_runs(dashboard_id, tile_ids)`, `add_api_key(row)` (raises `ValueError` when the id exists), `revoke_api_key(key_id, revoked_at)`, `get_api_key(key_id)`, `list_api_keys()`, then `lock(name, ttl_s)`, `close()` |
| `TraceStore` | `write(trace_id, document)`, `get(trace_id)`, `purge(older_than)` |
| `ResultStore` | `write(key, chunks)`, `open(key)`, `exists(key)`, `purge(older_than)`. Bytes only: the engine writes the Parquet. |

The models they exchange: `Entry` (`id`, `text`, `vector`, `metadata`), `Hit`
(an entry with its `distance`, and its `vector` when asked for), `Filter`
(`eq`, `ne` and `in`, all of which must hold), `BuildPointer` (which build
of a datasource is live, the `schema_version` it was built from, and the
`curated_hash` and `knowledge_hash` it was built with) and `Proposal` (one
row of the [review queue](../architecture/wave_b.md#5-the-review-queue): its
`id` is a hash of its kind, datasource, target and change, which
`Proposal.build(...)` computes). The engine embeds; a store keeps the
vectors it is handed.

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
    interface_version: ClassVar[int] = 4
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

## Interface: `PolicyCheck`

A policy check asks an outside service what a caller may read. The policies
config names one by `type` in its `check` block (see
[A policy check](../configuration/policies.md#a-policy-check)). It lives in
`nl2sql_sdk.policy`, with `UserContext` (who is asking) and `Grant` (`allow`
and `deny` patterns, written like a role's).

| Member | Description |
| :--- | :--- |
| `interface_version` | `1`. The engine accepts exactly the version it was built for. |
| `config_model` | A pydantic model for the `check` block apart from `type`. Set `extra="forbid"`, so a misspelt key is an error. |
| `from_config(cfg)` | Builds the check from a validated `config_model` instance, after the engine has resolved any `${provider:key}` in it. |
| `grant(user, datasource_id)` | Returns the `Grant` for `user` in `datasource_id`. Asked once per caller and datasource per run. Intersected with the roles; any exception denies the datasource for the run. |

Declare it in the `nl2sql.policy_checks` entry point group, or register it in
code with `registry.add_policy_check(name, cls)` or
`@registry.policy_check(name)`. No check is built in.

---

## Interface: `KnowledgeSource`

A knowledge source is where a glossary comes from: what people write about
their data (descriptions, synonyms, column roles, joins, business terms,
prose rules, example questions). The `knowledge` section names one per item
by `type` (see [Configuration](../configuration/system.md#knowledge)). It
lives in `nl2sql_sdk.knowledge`, which imports only pydantic and the
standard library, with the glossary's models (`Glossary`,
`DatasourceKnowledge`, `TableKnowledge`, `ColumnKnowledge`,
`JoinKnowledge`, `Term`, `Rule`), all strict. A source only reads.

| Member | Description |
| :--- | :--- |
| `interface_version` | `1`. The engine accepts exactly the version it was built for. |
| `config_model` | A pydantic model for the source's keys apart from `type`. Set `extra="forbid"`. |
| `from_config(cfg, *, folder)` | Builds the source from a validated `config_model` instance. `folder` is the folder of `nl2sql.yaml`. |
| `load()` | Returns the `Glossary`, read now. Called when the engine is built and on reload, never during a run. |

Every glossary text is at most 500 characters (the engine passes
`settings.knowledge.item_max_chars` as the validation context
`item_max_chars`); a longer one is a load error naming the item. Declare a
source in the `nl2sql.knowledge_sources` entry point group, or register it
in code with `registry.add_knowledge_source(name, cls)` or
`@registry.knowledge_source(name)`. The built-in `yaml` source arrives
with release 0.8.

---

## Optional: `DiscoverableAdapter`

An adapter can declare what it is found by, so `engine.datasources.scan()`
(release 0.9, [Wave C](../architecture/wave_c.md#finding-a-database-scan))
can offer it without the engine naming any database. It lives in
`nl2sql_sdk.discovery`, which imports only pydantic and the standard
library, with `Found` (`kind`: `url`, `file` or `port`, and its `value`). The
engine tests for it with `isinstance`, so an adapter without it is never found,
only chosen by name, and the adapter interface stays at version 2. All four
members are required together: leave a tuple empty for a way of being found
the adapter does not use. The five built-ins declare it.

`scan()` hands `connection_for` one `Found` at a time: the URL the caller
passed (whose scheme, up to any `+driver`, is in your `url_schemes`), a file
under the scanned folder with one of your `file_suffixes`, or `"127.0.0.1:port"`
for an open port in your `default_ports`. Return the `connection` fields it
fills in (the keys of your `connection_model`), or `None` when it is not yours.
Do not connect: read a file's first bytes at most. `scan()` lists the required
fields you did not fill as `missing`.

| Member | Description |
| :--- | :--- |
| `file_suffixes` | File suffixes it opens, such as `(".sqlite", ".sqlite3", ".db")`. |
| `default_ports` | TCP ports it listens on by default, such as `(5432,)`. Probed on `127.0.0.1` only. |
| `url_schemes` | Connection URL schemes, up to any `+driver`, such as `("postgresql", "postgres")`. |
| `connection_for(found)` | A classmethod: the connection fields this `Found` fills in, or `None` when it is not this adapter's (a `.db` file that is not SQLite). It never connects. |

---

## The conformance kit: `nl2sql_sdk.testing`

`AdapterConformance`, installed with `pip install "nl2sql-sdk[testing]"`
(adds pytest and sqlglot), is a pytest test class an adapter's own tests
subclass. It checks what this page promises through `DatasourceAdapter`
alone: the dialect, the connection model, `test_connection`'s stages, the
index scope, profiling, bound parameters, every declared capability, error
classification and streaming. Like the SQLAlchemy base, it is an extra: the
SDK core never imports it. How to run it, with its fixtures and the full
list of checks: [Running the conformance kit](development.md#running-the-conformance-kit).
