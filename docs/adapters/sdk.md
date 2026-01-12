# Adapter SDK Reference

The **Adapter SDK** (`nl2sql-adapter-sdk`) defines the core contract that all datasources must implement.

## Interface: `DatasourceAdapter`

All adapters must inherit from `nl2sql_adapter_sdk.interfaces.DatasourceAdapter`.

```python
from nl2sql_adapter_sdk import DatasourceAdapter
```

### Mandatory Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `datasource_id` | `str` | Unique identifier (e.g., "production_db"). |
| `row_limit` | `int` | **Safety Breaker**. Must return a safe limit (e.g., 1000) to prevent OOM errors. |
| `max_bytes` | `int` | **Safety Breaker**. Recommended limit for network payloads. |

### Mandatory Methods

#### `fetch_schema()`

Returns `SchemaMetadata`.

* **Returns**: `SchemaMetadata` containing tables, columns, PKs, FKs.
* **Requirement**: Must populate `col.statistics` (samples, min/max) for the validation logic to work effectively.

#### `execute(sql: str)`

Executes a query and returns results.

* **Args**: `sql` (str) - The SQL query to run.
* **Returns**: `QueryResult` with `rows` (list of dicts) and `columns` (list of names).

#### `dry_run(sql: str)`

Validates SQL without executing it (or safely rolling back).

* **Args**: `sql` (str)
* **Returns**: `DryRunResult(is_valid=bool, error_message=str)`

### Optional Methods

#### `explain(sql: str)`

Returns the execution plan.

* **Returns**: `QueryPlan(plan_text=str)`

#### `cost_estimate(sql: str)`

Returns cost/row estimates for the Physical Validator.

* **Returns**: `CostEstimate(estimated_cost=float, estimated_rows=int)`

---

## Compliance Testing

The SDK provides a compliance test suite. **All Adapters MUST pass this suite.**

It verifies:

* Schema Introspection (PKs/FKs detected?)
* Type Mapping (Date -> Python Date, Numeric -> Python Float)
* Error Handling (Bad SQL -> AdapterError)

### Running Tests

```python
# tests/test_my_adapter.py
from nl2sql_adapter_sdk.testing import BaseAdapterTest
from my_adapter import MyAdapter
import pytest

class TestMyAdapter(BaseAdapterTest):
    @pytest.fixture
    def adapter(self):
        return MyAdapter(...)
```
