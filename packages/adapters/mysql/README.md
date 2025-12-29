# NL2SQL MySQL Adapter

This package (`nl2sql-mysql`) provides MySQL support for the NL2SQL engine.

## 🔌 Features

* **Driver**: Uses `pymysql` (pure Python).
* **MySQL Syntax**: Handles backtick quoting and MySQL-specific functions.

## 📦 Installation

```bash
pip install -e packages/adapters/mysql
```

## ⚙️ Configuration

In your `datasources.yaml`:

```yaml
- id: my_mysql_db
  engine: mysql
  sqlalchemy_url: "mysql+pymysql://user:password@localhost:3306/mydb"
```
