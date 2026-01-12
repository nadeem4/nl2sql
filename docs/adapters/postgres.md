# PostgreSQL Adapter

The Postgres adapter is the **Gold Standard** adapter for the platform. It supports the full set of optimization features including `EXPLAIN`-based dry runs and cost estimation.

This adapter extends `BaseSQLAlchemyAdapter`, leveraging automatic schema reflection and statistics gathering.

## Configuration

**Type**: `postgres` (or `postgresql`)

```yaml

connection:
  type: "postgres"
  host: "localhost"
  port: 5432
  user: "postgres"
  password: "${env:DB_PASS}"
  database: "my_db"
  options:
    sslmode: "require" # Optional: passed to query string
```

### Connection Details

* **Driver**: `psycopg2` (via `sqlalchemy`).
* **URI Constructed**: `postgresql://{user}:{pass}@{host}:{port}/{db}?{options}`

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | Native `-c statement_timeout={ms}` | Enforced server-side. |
| **Dry Run** | `EXPLAIN {sql}` | Highly accurate validation. |
| **Costing** | `EXPLAIN (FORMAT JSON) {sql}` | Returns "Total Cost" and "Plan Rows". |
| **Stats** | Optimized Queries | Fetches `null_perc`, `distinct`, `min/max`. |

### Optimization Details

The Postgres adapter leverages native `EXPLAIN` capabilities for robust validation and estimation:

* **Dry Run**: Implemented via `EXPLAIN {sql}`. This validates the SQL syntax and ensures that all tables/columns exist without actually executing the query.
* **Explain**: Uses `EXPLAIN (FORMAT JSON) {sql}` to retrieve the full query execution plan in structured JSON format.
* **Cost Estimate**: Uses the same `EXPLAIN (FORMAT JSON) {sql}` command. It parses the root `Plan` object to extract:
  * `Total Cost`: Used as the query cost proxy.
  * `Plan Rows`: Used as the estimated result size.

## Troubleshooting

### SSL Verification

If connecting to Azure or AWS RDS with strict SSL, ensure you pass the CA certificate path in options or standard libpq environment variables, or use `sslmode: disable` for testing.
