# NL2SQL PostgreSQL Adapter

This package (`nl2sql-postgres`) provides PostgreSQL support for the NL2SQL engine.

## 🔌 Features

* **Driver**: Uses `psycopg2-binary`.
* **Full Capability**: Supports `LIMIT/OFFSET`, `CTEs`, and Window Functions.
* **Dry Run**: Implements `EXPLAIN` based validation.

## 📦 Installation

```bash
pip install -e packages/adapters/postgres
```

## ⚠️ Prerequisites

This adapter relies on `psycopg2`.

### Linux (Ubuntu/Debian)

You need the PostgreSQL development headers:

```bash
sudo apt-get install libpq-dev
```

### Windows

The binary wheel `psycopg2-binary` usually works out of the box. If compiling from source, ensure PostgreSQL binaries are in your PATH.

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_postgres_db
  engine: postgres
  sqlalchemy_url: "postgresql+psycopg2://user:password@localhost:5432/mydb"
```
