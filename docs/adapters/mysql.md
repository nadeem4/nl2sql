# MySQL Adapter

Support for MySQL 5.7+ and 8.0+.

!!! info "Implementation"
    This adapter extends `SQLAlchemyAdapter`. It overrides `build_engine()` to set the `MAX_EXECUTION_TIME` session variable.

## Configuration

**Type**: `mysql`

```yaml
# datasources/my_db.yaml
type: mysql
connection:
  host: localhost
  port: 3306
  user: root
  password: "${env:DB_PASS}"
  database: my_db
```

The keys are `MysqlConnection`'s (`nl2sql.adapters.mysql.adapter`), and any
other key is an error; see [Connection](../configuration/datasources.md#connection).

### Connection Details

* **Driver**: `pymysql` (Pure Python).
* **URI Constructed**: `mysql+pymysql://{user}:{pass}@{host}:{port}/{db}`.
  Built with SQLAlchemy's `URL.create`, so the user name and password are
  percent-escaped: a password containing `@ : / # %` works as written.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | `SET MAX_EXECUTION_TIME={ms}` | Session-level enforcement. |
| **Row counts** | `information_schema.tables.table_rows` | InnoDB's estimate; `ANALYZE TABLE` refreshes it. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |
