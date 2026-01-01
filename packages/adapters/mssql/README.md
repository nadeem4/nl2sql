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

## ⚠️ Prerequisites (ODBC Driver)

This adapter requires the **ODBC Driver 17 for SQL Server**.

### Windows

Download and install from [Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).

### Linux (Ubuntu/Debian)

```bash
sudo su
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list
exit
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql17
```

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_mssql_db
  engine: mssql
  sqlalchemy_url: "mssql+pyodbc://sa:Password@localhost:1433/mydb?driver=ODBC+Driver+17+for+SQL+Server"
```
