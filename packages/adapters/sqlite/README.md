# NL2SQL SQLite Adapter

This package (`nl2sql-sqlite`) provides SQLite support for the NL2SQL engine.

## 🔌 Features

* **Driver**: Uses Python's built-in `sqlite3` (via SQLAlchemy).
* **Zero Config**: Works with local file paths.
* **Testing**: Used as the default engine for unit tests.

## 📦 Installation

```bash
pip install -e packages/adapters/sqlite
```

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_local_db
  engine: sqlite
  sqlalchemy_url: "sqlite:///path/to/database.db"
```
