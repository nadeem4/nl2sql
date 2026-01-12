# SQLite Adapter

Simple file-based adapter for local development and testing.

!!! info "Implementation"
    This adapter extends `BaseSQLAlchemyAdapter`. Note that `timeout` configurations apply to the *database lock*, not query execution time.

## Configuration

**Type**: `sqlite`

```yaml

connection:
  type: "sqlite"
  database: "./my_data.db" # Absolute or relative path
```

!!! warning "Persistence"
    If using **Docker**, avoid relative paths like `./my_data.db` as they will be lost on container restart. Use an absolute path mapped to a volume, e.g., `/app/data/my_data.db`.

### Connection Details

* **Driver**: Built-in `sqlite3`.
* **URI Constructed**: `sqlite:///{database}`

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | `connect_args["timeout"]` | Controls *Locking* timeout, not execution. |
| **Dry Run** | `EXPLAIN QUERY PLAN` | Validates parsing (rudimentary). |
| **Costing** | Stubbed | Returns default cost=1.0. |

### Optimization Details

* **Dry Run**: Uses `EXPLAIN QUERY PLAN {sql}`. If this command succeeds, the SQL syntax is valid.
* **Explain**: Currently stubbed (returns a simple message) as SQLite's explain output is not in a standardized, easily parsable format like JSON or XML.
* **Cost Estimate**: Stubbed. Returns a fixed cost of `1.0` and `10` estimated rows, as SQLite does not expose cost metrics comfortably.

## Hints

* **Concurrency**: SQLite is poor at high concurrency. Use for **Lite Mode** or single-user testing only.
