# Adapter Interface Reference

The adapter contract lives in the SDK, `nl2sql-sdk` (imported as `nl2sql_sdk`):

- `nl2sql.datasources.protocols.DatasourceAdapterProtocol`
- `nl2sql_sdk.contracts.AdapterRequest`
- `nl2sql_sdk.contracts.ResultFrame`

## Interface: `DatasourceAdapterProtocol`

Adapters expose a capability-driven interface:

```python
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.capabilities import DatasourceCapability
```

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

The hook is an optional protocol rather than a new required method so that the
SDK change is additive: `DatasourceAdapterProtocol` is unchanged, and no
third-party adapter has to be touched or re-released.

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

## Compliance Testing

All adapters should pass the compliance test suite (schema introspection, type mapping,
error handling, and result contract validation).
