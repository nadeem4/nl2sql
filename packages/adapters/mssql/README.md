# NL2SQL MSSQL Adapter

This package (`nl2sql-mssql`) provides Microsoft SQL Server support for the NL2SQL engine.

## 🔌 Features

* **Driver**: Uses `pyodbc` with the generic ODBC Driver 17.
* **T-SQL Syntax**: Generates T-SQL compatible queries (e.g., `TOP` instead of `LIMIT`).
* **Schema Reflection**: Filters out system and temporary tables.

## 📦 Installation

```bash
pip install -e packages/adapters/mssql
```

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_mssql_db
  engine: mssql
  sqlalchemy_url: "mssql+pyodbc://sa:Password@localhost:1433/mydb?driver=ODBC+Driver+17+for+SQL+Server"
```
