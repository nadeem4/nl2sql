# NL2SQL Adapter SDK

The **NL2SQL Adapter SDK** (`nl2sql-adapter-sdk`) defines the contract (Protocol) that all database adapters must implement. It ensures that the Core engine remains agnostic to specific database technologies.

## 🛠️ The Contract

Any adapter to be used with NL2SQL must implement the `DatasourceAdapter` protocol:

```python
class DatasourceAdapter(Protocol):
    def fetch_schema(self) -> SchemaMetadata:
        ...
    
    def execute(self, sql: str) -> QueryResult:
        ...
```

## 📦 Installation

```bash
pip install -e packages/adapter-sdk
```

## 🏗️ Architecture: Why Separate SDK?

We distinguish between the **Contract** (`adapter-sdk`) and the **Implementation** (`adapter-sqlalchemy`).

* **`adapter-sdk`**: Lightweight, zero-dependency. Defines *WHAT* an adapter must do. Use this for non-SQL sources (APIs, CSVs, Mongo).
* **`adapter-sqlalchemy`**: Heavy implementation logic. Defines *HOW* to do it for SQL databases (Postgres, MySQL, etc.).

This allows the core system to support diverse backend types without being tied to Relational Logic.

## 🧪 Compliance Testing

This package includes a standard compliance suite (`AdapterComplianceSuite`) that adapters can inherit from to ensure they met the spec.

```python
# tests/test_my_adapter.py
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite

class TestMyAdapter(AdapterComplianceSuite):
    ...
```
