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
        
    def capabilities(self) -> CapabilitySet:
        ...
```

## 📦 Installation

```bash
pip install -e packages/adapter-sdk
```

## 🧪 Compliance Testing

This package includes a standard compliance suite (`AdapterComplianceSuite`) that adapters can inherit from to ensure they met the spec.

```python
# tests/test_my_adapter.py
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite

class TestMyAdapter(AdapterComplianceSuite):
    ...
```
