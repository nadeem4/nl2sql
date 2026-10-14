# SQLite Adapter

Simple file-based adapter for local development and testing.

!!! info "Implementation"
    This adapter extends `SQLAlchemyAdapter`. It opens the file read-only and stops a statement that runs past `limits.timeout_ms` with a progress handler.

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
| **Read-only** | `mode=ro` | Always; declares `ENFORCES_READ_ONLY`. |
| **Timeout** | A progress handler per statement | See [Statement Timeout](#statement-timeout). Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | The message | A file that cannot be opened is `database`. |
| **Row counts** | `COUNT(*)` | SQLite keeps no estimate, so rows are counted under `profile: stats`, and there is no row count under `catalog`. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |

### Statement Timeout

SQLite has no statement timeout: its own `timeout` is how long to wait for a
lock. So when `limits.timeout_ms` is set, each statement installs a progress
handler with its own deadline. SQLite calls it every 10,000 steps of its
virtual machine, while the statement runs and while its rows are fetched; once
the deadline has passed, the handler stops the statement, which fails with
"interrupted" and is reported as a timeout (`QUERY_TIMEOUT`) that the engine
does not retry. The next statement on the connection gets a new deadline.

## Hints

* **Concurrency**: SQLite is poor at high concurrency. Use for **Lite Mode** or single-user testing only.
