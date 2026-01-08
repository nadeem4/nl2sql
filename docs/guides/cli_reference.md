# CLI Reference

The `nl2sql` Command Line Interface (CLI) is the primary way to interact with the platform.

## Global Options

These flags apply to all commands and must be specified **before** the subcommand.

* `--env`, `-e TEXT`: Environment name (e.g. `dev`, `demo`, `prod`). Isolates configurations and vector stores. Defaults to `default` (Production).

Example:

```bash
# Uses configs/datasources.yaml
nl2sql run "query"

# Uses configs/datasources.demo.yaml
nl2sql --env demo run "query"
```

## Commands

### `setup`

Interactive wizard to initialize the platform.

```bash
nl2sql setup [OPTIONS]
```

**Options:**

* `--demo`: **Quickstart Mode**. Automatically generates a "Manufacturing" demo environment with 4 SQLite databases and sample questions.
* `--docker`: Used with `--demo` to generate a `docker-compose.yml` for full-fidelity testing (Postgres/MySQL) instead of SQLite.

### Example: Try it Now

```bash
nl2sql setup --demo
```

### `index`

Indexes database schemas and examples into the vector store for retrieval.

```bash
nl2sql index [OPTIONS]
```

**Features:**

* **Schema Indexing**: Introspects tables, columns, foreign keys, and comments.
* **Example Indexing**: Indexes sample questions for few-shot routing.
* **Granular Feedback**: Displays a checklist of indexed items per datasource.
* **Summary Table**: Shows total tables, columns, and examples indexed.

**Options:**

* `--config PATH`: Path to datasource config.
* `--vector-store PATH`: Path to vector store directory.

**Example:**

```bash
nl2sql --env demo index
```

### `run`

Executes a natural language query against the configured datasources.

```bash
nl2sql run [QUERY] [OPTIONS]
```

**Arguments:**

* `QUERY`: The natural language question (e.g. "Show me active users").

**Options:**

* `--role TEXT`: The RBAC role to assume (e.g. `admin`, `analyst`). Defaults to `admin`.
* `--no-exec`: Plan and Validate only, do not execute SQL.
* `--verbose`, `-v`: Show detailed reasoning traces and intermediate node outputs.
* `--show-perf`: Display timing metrics.

**Example:**

```bash
nl2sql run "Who bought the Bolt M5?" --role sales_analyst --verbose
```

### `policy`

Manage RBAC policies.

* `validate`: Validates syntax and integrity of `policies.json`.

```bash
nl2sql policy validate
```

### `doctor`

Diagnoses environment issues (Python version, missing adapters, connectivity).

```bash
nl2sql doctor
```

### `benchmark`

Runs the evaluation suite against a "Golden Dataset".

```bash
nl2sql benchmark --dataset configs/benchmark.yaml
```
