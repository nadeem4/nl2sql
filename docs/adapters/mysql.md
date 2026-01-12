# MySQL Adapter

Support for MySQL 5.7+ and 8.0+.

!!! info "Implementation"
    This adapter extends `BaseSQLAlchemyAdapter`. It overrides `connect()` to handle `MAX_EXECUTION_TIME` session variables.

## Configuration

**Type**: `mysql`

```yaml

connection:
  type: "mysql"
  host: "localhost"
  port: 3306
  user: "root"
  password: "${env:DB_PASS}"
  database: "my_db"
  options:
    charset: "utf8mb4"
```

### Connection Details

* **Driver**: `pymysql` (Pure Python).
* **URI Constructed**: `mysql+pymysql://{user}:{pass}@{host}:{port}/{db}?{options}`

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | `SET MAX_EXECUTION_TIME={ms}` | Session-level enforcement. |
| **Dry Run** | Transaction Rollback | Starts transaction, runs SQL, rolls back. |
| **Costing** | `EXPLAIN FORMAT=JSON` | Extracts `query_cost`. |
| **Stats** | `SELECT count(*), min(), max()` | Standard aggregation. |

### Optimization Details

* **Dry Run**: Uses a Transaction Rollback strategy. It starts a transaction (`BEGIN`), executes the query, and immediately rolls back (`ROLLBACK`). **Note**: This means the query *is* technically executed, but its effects are reversed.
* **Explain**: Uses `EXPLAIN FORMAT=JSON {sql}` to get the execution plan.
* **Cost Estimate**: Parses the JSON output to extract `query_cost`. MySQL does not reliably provide a global "estimated rows" count for complex queries, so this is often returned as 0.

## Limitations

* **Row Estimation**: MySQL's `EXPLAIN` does not always provide a reliable "Total Rows" estimate for complex joins compared to Postgres.
