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

Returns cost/row estimates for the Physical Validator.

---

## Compliance Testing

All adapters should pass the compliance test suite (schema introspection, type mapping,
error handling, and result contract validation).
