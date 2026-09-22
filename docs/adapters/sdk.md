# Adapter Interface Reference

The adapter contract lives in the adapter SDK:

- `nl2sql.datasources.protocols.DatasourceAdapterProtocol`
- `nl2sql_adapter_sdk.contracts.AdapterRequest`
- `nl2sql_adapter_sdk.contracts.ResultFrame`

## Interface: `DatasourceAdapterProtocol`

Adapters expose a capability-driven interface:

```python
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql_adapter_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
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

Declares supported capabilities (e.g., `supports_sql`, `supports_rest`).

#### `execute(request: AdapterRequest) -> ResultFrame`

Executes a plan-specific request and returns a normalized `ResultFrame`.

* **Args**: `AdapterRequest` with `plan_type` and `payload`
* **Returns**: `ResultFrame` with `columns`, `rows`, `row_count`, and error metadata

#### `fetch_schema_snapshot()`

Required only if `supports_schema_introspection` is advertised.

### Optional Methods (SQL adapters)

#### `dry_run(sql: str)`

Validates SQL without executing it (or safely rolling back).

#### `explain(sql: str)`

Returns the execution plan.

#### `cost_estimate(sql: str)`

Returns cost/row estimates. Advertised via the `SUPPORTS_COST_ESTIMATE` capability; the pipeline does not currently call it.

### Optional hook: `render_sql(expression)`

`nl2sql_adapter_sdk.protocols.SqlRenderingAdapterProtocol`. The engine builds
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
