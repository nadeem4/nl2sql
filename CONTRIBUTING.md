# Contributing to NL2SQL

Welcome to the `nl2sql` monorepo! This project is a production-grade Natural Language to SQL engine.

## 🏗️ Monorepo Structure

* `packages/core`: The main query engine (LangGraph, core Logic).
* `packages/adapter-sdk`: The interface definition for database adapters.
* `packages/adapters/*`: Implementation of database drivers (Postgres, MSSQL, MySQL, etc.).

## 🚀 Getting Started

### Prerequisites

* Python 3.10+
* Docker & Docker Compose (for integration tests)

### Installation

1. **Clone the repo**:

    ```bash
    git clone https://github.com/nadeem4/nl2sql.git
    cd nl2sql
    ```

2. **Create a Virtual Environment**:

    ```bash
    python -m venv venv
    .\venv\Scripts\activate   # Windows
    source venv/bin/activate  # Linux/Mac
    ```

3. **Install Editable Packages**:

    ```bash
    pip install -r requirements.txt
    ```

## 🧪 Testing

### Running Unit Tests

```bash
pytest packages/core/tests/unit
```

### Running Integration Tests

This requires Docker. It will spin up REAL databases and run the Compliance Suite against them.

```powershell
./scripts/test_integration.ps1
```

## 🤝 Contribution Workflow

1. Create a branch `feat/your-feature`.
2. Make changes in the relevant package.
3. **Run Tests** to ensure no regression.
4. Submit a Pull Request.

## 🧩 Creating a New Adapter

We have two base classes for adapters. Choose the right one to keep dependencies minimal:

| Base Class | Package | Use Case | Dependencies |
| :--- | :--- | :--- | :--- |
| `BaseSQLAlchemyAdapter` | `adapter-sqlalchemy` | **Relational Databases** (Postgres, Snowflake, Oracle, etc.) | High (`SQLAlchemy`) |
| `DatasourceAdapter` (Protocol) | `adapter-sdk` | **Everything Else** (REST APIs, Mongo, CSV, GraphDBs) | None |

### 1. SQL Database Adapter

Inherit from `BaseSQLAlchemyAdapter`. It handles `fetch_schema` and `execute` for you.

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class MyDbAdapter(BaseSQLAlchemyAdapter):
    def connect(self, config):
        # ... implementation ...
```

### 2. General Adapter (API/NoSQL)

Implement the `DatasourceAdapter` protocol directly.

```python
from nl2sql_adapter_sdk import DatasourceAdapter

class MyApiAdapter(DatasourceAdapter):
    # You MUST implement fetch_schema, execute, etc. yourself
```
