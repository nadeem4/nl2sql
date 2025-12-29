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

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_postgres_db
  engine: postgres
  sqlalchemy_url: "postgresql+psycopg2://user:password@localhost:5432/mydb"
```
