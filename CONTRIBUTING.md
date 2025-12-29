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

1. Copy `packages/adapters/sqlite` as a template.
2. Implement `DatasourceAdapter` methods (`fetch_schema`, `execute`, etc.).
3. Register your entry point in `pyproject.toml` under `[project.entry-points."nl2sql.adapters"]`.
