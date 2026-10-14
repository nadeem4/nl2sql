# SQLite Adapter

Simple file-based adapter for local development and testing.

!!! info "Implementation"
    This adapter extends `SQLAlchemyAdapter`. Note that `timeout` configurations apply to the *database lock*, not query execution time.

## Configuration

**Type**: `sqlite`

```yaml
# datasources/my_data.yaml
type: sqlite
connection:
  database: ./my_data.db # Absolute or relative path
```

`database` is the only key (`SqliteConnection`); any other is an error.

A relative `database` is read from the folder of `nl2sql.yaml` when the config
was loaded from a file (so `nl2sql init --demo` can write `data/chinook.sqlite`),
and from the working directory when it was built in code. The DuckDB adapter
does the same.

!!! warning "Persistence"
    If using **Docker**, avoid relative paths like `./my_data.db` as they will be lost on container restart. Use an absolute path mapped to a volume, e.g., `/app/data/my_data.db`.

### Connection Details

* **Driver**: Built-in `sqlite3`.
* **URI Constructed**: `sqlite:///file:{database}?mode=ro&uri=true`. The file is
  always opened read-only: the driver refuses every write before any SQL is
  parsed, and a file that does not exist is an error rather than a new, empty
  database. There is no setting to turn it off; the engine never writes.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | `connect_args["timeout"]` | Controls *Locking* timeout, not execution. |
| **Row counts** | `COUNT(*)` | SQLite keeps no estimate, so rows are counted under `profile: stats`, and there is no row count under `catalog`. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |

## Hints

* **Concurrency**: SQLite is poor at high concurrency. Use for **Lite Mode** or single-user testing only.
