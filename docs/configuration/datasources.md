# Datasource Configuration

The `datasources` section of [`nl2sql.yaml`](system.md) lists the databases
the engine can query. `nl2sql init` writes it as a folder, one file per
datasource, so each team can own its own database's file:

```yaml
# nl2sql.yaml
datasources: { file: datasources/ }
```

```yaml
# datasources/sales.yaml   (the id defaults to the file name: "sales")
type: postgres
description: Orders, customers and revenue for the EU shop.
connection:
  host: db.internal
  port: 5432
  database: sales
  user: nl2sql
  password: "${env:SALES_DB_PASSWORD}"
limits: { row_limit: 1000, timeout_ms: 8000, max_bytes: 10485760 }
index:
  exclude_tables: ["*.*_audit"]
preferred_tables: [public.revenue_daily, v_monthly_sales]
domains: [revenue, customers, orders]
sample_questions:
  - What was revenue by region last quarter?
  - Which customers placed the most orders this year?
required: true
```

The section can also be a file holding the list
(`datasources: {file: datasources.yaml}`), or the list written inline in
`nl2sql.yaml`. An empty list is valid: the apps add datasources at runtime.

## Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | the file name in a folder, otherwise required | The datasource's id. In a folder, if it is given it must equal the file name. |
| `type` | required | The adapter: `sqlite`, `duckdb`, `postgres`, `mysql`, `mssql`, or an adapter a plugin registers. A misspelt or uninstalled one is a `ConfigError` with "did you mean" and the install hint. |
| `description` | none | What is in the database. The resolver's answerability check reads it, so write one. |
| `connection` | `{}` | The adapter's connection settings, validated against its connection model; see [Connection](#connection). Every value may be a `${provider:key}` reference and keeps it until the adapter is built. A relative SQLite or DuckDB `database` is relative to `nl2sql.yaml`. |
| `limits` | none | `row_limit`, `timeout_ms`, `max_bytes`; see [Limits](#limits). |
| `index` | everything | What of the database is indexed; see [Index](#index). |
| `preferred_tables` | `[]` | Tables or views the planner should reach for first. See [Preferred tables](#preferred-tables). |
| `sample_questions` | `[]` | Questions the datasource answers. They are indexed into its `schema.datasource` entry, which routing reads. |
| `domains` | `[]` | A routing hint written by people. Routing does not read it yet. |
| `required` | `false` | `engine.check()` fails (`EngineNotReady`) when this datasource cannot connect, and the CLI, `nl2sql-api` and the demo refuse to start. See [Startup](system.md#startup-check-and-required). |

In Python, `Datasource(id=..., adapter=MyAdapter(...))` passes a built adapter
object instead of `type`.

## Connection

Each adapter declares its `connection` keys in a connection model, and the
keys are strict: a key the model does not have is an error when the engine is
built, with the field it was probably meant to be.

```text
datasources[0].connection: connection.hostname: unknown key 'hostname'. Did you mean 'host'?
```

| Adapter | Keys |
| --- | --- |
| `sqlite` | `database` (the file) |
| `duckdb` | `database` (the file, or `:memory:`) |
| `postgres` | `host`, `port` (5432), `database`, `user`, `password`, `sslmode` (`disable`, `prefer` (default), `require`, `verify-full`) |
| `mysql` | `host`, `port` (3306), `database`, `user`, `password` |
| `mssql` | `host`, `port` (1433), `database`, `user`, `password`, `driver` (`ODBC Driver 17 for SQL Server`), `trusted_connection` (false), `trust_server_certificate` (false) |

`postgres`, `mysql` and `mssql` also take `pool`, the connection pool:

```yaml
connection:
  host: db.internal
  # ...
  pool: { size: 5, max_overflow: 0, timeout_s: 30, recycle_s: 1800 }   # the defaults
```

| Key | Default | Meaning |
| --- | --- | --- |
| `size` | 5 | Connections kept open |
| `max_overflow` | 0 | Extra connections allowed under load |
| `timeout_s` | 30 | How long a query waits for a free connection |
| `recycle_s` | 1800 | A connection older than this is replaced |

With no overflow, `size` is also the most queries NL2SQL runs against that
database at once, so it cannot overload a production server. A SQLite or
DuckDB file has no pool.

In Python, `connection` may also be the adapter's model, used as given:

```python
from nl2sql.adapters.postgres.adapter import PostgresConnection

Datasource(id="sales", type="postgres",
           connection=PostgresConnection(host="db.internal", database="sales", user="nl2sql",
                                         password=os.environ["SALES_DB_PASSWORD"]))
```

The model's JSON Schema (`PostgresConnection.model_json_schema()`) describes
the form for connecting that database; a password is `"format": "password"`.

## Limits

```yaml
limits:
  row_limit: 1000       # the most rows one sub-query returns
  timeout_ms: 8000      # the longest one statement may run
  max_bytes: 10485760
```

- `timeout_ms`: every built-in adapter enforces it (SQL Server
  rounds up to whole seconds; SQLite stops a statement with a progress handler).
  Every session is read-only where the database allows it, with no setting; SQL Server cannot, and `check()` warns. See each [adapter page](../adapters/index.md) for how.
- `row_limit`: unset, it is `settings.results.row_limit` (100,000); set higher,
  `settings.results.row_limit` still bounds it. A result that reaches it is
  flagged as truncated and the answer says so. It is applied before results
  are combined, so a join input that reaches it is refused
  (`RESULT_TRUNCATED`) and a union input that reaches it is flagged with a
  warning; see [row cap before a combine](../architecture/nodes/engine_aggregator_node.md#row-cap-before-a-combine).
- `max_bytes`: passed to the adapter.

There is one place for each limit: the 0.4 `options:` bag and the top-level
spellings are gone.

## Index

`index` says what of the database is indexed. Every key is optional, and an
unknown key is an error.

```yaml
index:
  include_schemas: [public, reporting]
  exclude_tables: ["*.*_audit", "public.tmp_*"]
  exclude_columns: ["public.customers.email"]
```

| Key | Default | Meaning |
| --- | --- | --- |
| `include_schemas` | every schema but the database's system schemas | Schema globs |
| `exclude_schemas` | none | Schema globs, applied after include |
| `include_tables`, `exclude_tables` | all, none | `schema.table` globs; a pattern with no schema is an error with the fix (`'*.tmp_*'`) |
| `exclude_columns` | none | `schema.table.column` globs: the column is neither indexed nor profiled. This is not access control: use [policies](policies.md). |
| `profile` | `stats` | `catalog`: names, types, keys, comments and the catalog's row estimates, with no query against table data. `stats`: also each column's null share, distinct count and min/max. |
| `sample_values` | `false` | Store each text column's values: every one when there are 50 or fewer distinct, otherwise the five most frequent. Off by default, since values reach the index, the prompts and the model provider. The demo (`NL2SQL.demo()`, `nl2sql init --demo`) turns it on. |
| `max_profile_rows` | 1,000,000 | A table estimated above this is profiled from the catalog only, and the index build reports it |

Every glob ignores case, and an exclude wins over an include. A SQLite
database has one schema, `main`.

The scope is applied before any query that reads a schema, so an excluded
schema costs nothing. A row count is the catalog's estimate where the
database keeps one (PostgreSQL, MySQL, SQL Server, DuckDB); otherwise, under
`stats`, the rows are counted. What a table or column was indexed without (a
statistics query the database rejected, a table over `max_profile_rows`) is
an issue: `nl2sql index` prints it, and `engine.index.build()` returns it in
the datasource's stats under `issues`. See
[SQLAlchemy adapters](../adapters/sqlalchemy.md#reading-a-schema) for how
the built-in adapters read a schema.

## Preferred tables

The package never creates a view. The data team creates it in the database,
lists it under `preferred_tables`, and runs `nl2sql index`; every built-in
adapter snapshots views as well as tables (PostgreSQL materialized views are
not read). At question time the schema retriever lists preferred tables first
and marks them `"preferred": true` in the schema the planner sees, and the
planner is told to plan against a preferred table when it answers the
question:

- When the whole schema is sent (`settings.retrieval.full_snapshot_max_tables`
  tables or fewer), every preferred table is in it, first.
- Otherwise the retrieved tables are re-ordered so preferred ones come first,
  and a preferred table the search scored but did not pick is added when its
  similarity is within 0.05 of the least similar pick.

RBAC is unchanged: a preferred table the role may not read loses its data like
any other table, and the validator refuses a plan that reads it.

A name that is not in the indexed schema is a warning, not an error: `nl2sql
doctor` and the index status (`engine.index.health()`, `GET /api/v1/index/status`)
print "Preferred table 'v_x' of 'chinook' is not in the indexed schema". The
usual cause is a view created after the last index; re-index the datasource.

## When a datasource fails to start

A datasource the engine cannot build (an unknown `type`, a missing driver, an
invalid connection config, a `${...}` reference that does not resolve) stops
it: building the engine raises one `ConfigError` that names every such
datasource and the reason. Building never connects, so this needs no database.

A datasource that cannot **connect** is found by `engine.check()` (see
[Public Facade → Lifecycle](../api/core/public-facade.md#lifecycle)), and does
not stop the engine unless it is `required`. The others stay routed, and the
broken one is marked unavailable:

- the log has an error: `Datasource 'sales' is unavailable: <reason>`;
- `check()` reports it `failed` at stage `connect`, with a fix;
- `engine.datasources.list()` (and `GET /api/v1/datasource`) shows it with
  `status: "failed"` and the `reason`, and nothing is routed to it;
- the index status (`engine.index.health()`, `GET /api/v1/index/status`) carries
  the same sentence as a warning;
- a query with `datasource_id` set to it, and `nl2sql index` or
  `engine.index.build([...])` naming it, fail with that reason.

`check()` raises `EngineNotReady` when a `required: true` datasource fails, or
when datasources are configured and none is ready. A later `check()`, or
`engine.datasources.test(id)`, that connects routes the datasource again.

`nl2sql doctor` prints the same `check()` report. After `check()`,
`engine.policies.validate()` reports a role that names an unavailable
datasource as `UNAVAILABLE`.

A secret provider that cannot start is different: it fails startup. See [Secrets → When a provider fails to start](secrets.md#when-a-provider-fails-to-start).

## Notes

- Use `${env:VAR}` for environment variables, or `${provider:key}` for a secret
  from a provider in the [`secrets` section](secrets.md).
- The keys under `connection` are the adapter's; see [Connection](#connection).
- From Python, `engine.datasources.add(Datasource(...))` adds one to a running
  engine (built, not connected), and `engine.datasources.remove(id)` takes one
  out; `nl2sql-api`'s `POST /api/v1/datasource` takes
  the same shape as JSON.
