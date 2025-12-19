# ExecutorNode

## Purpose

The `ExecutorNode` is responsible for waiting for a SQL query (draft) and executing it against the actual database engine. It acts as the final "Effector" in the pipeline. It strictly enforces security protocols to prevent mutation or data loss.

## Components

- **`DatasourceRegistry`**: To obtain the database engine/connection.
- **`enforce_read_only`**: Security utility to scan for forbidden SQL keywords (INSERT, UPDATE, DROP, etc.).
- **`engine_factory.run_read_query`**: Helper to execute the query.

## Inputs

The node reads the following fields from `GraphState`:

- `state.sql_draft`: The SQL query string to execute.
- `state.datasource_id`: ID of the target datasource.

## Outputs

The node updates the following fields in `GraphState`:

- `state.execution`: A structured `ExecutionModel` containing:
  - `row_count`: Number of rows returned.
  - `rows`: List of dictionaries representing the result set.
  - `columns`: List of column names.
  - `error`: String description of any database error.
- `state.reasoning`: Log entry summarizing the execution stats.
- `state.errors`: Appends `PipelineError` if security check fails or DB throws an error.

## Logic Flow

1. **Validation**: Checks if `sql_draft` and `datasource_id` are present.
2. **Datasource Resolution**: Identifies the primary datasource if a list was provided.
3. **Security Check**:
    - Detects the dialect based on the profile.
    - Calls `enforce_read_only` to validate the SQL.
    - If violation is found, returns `SECURITY_VIOLATION` critical error.
4. **Execution**:
    - Uses SQLAlchemy engine to run the query.
    - Fetches all results and maps them to a list of dictionaries.
    - Captures metadata (column names).
5. **Result Packaging**: Wraps results or exceptions into the `ExecutionModel`.

## Error Handling

- **`MISSING_SQL`**: If generator failed to produce output.
- **`SECURITY_VIOLATION`**: If DML/DDL keywords are detected.
- **`DB_EXECUTION_ERROR`**: Runtime errors from the database (e.g., syntax error, invalid table).
- **`EXECUTOR_CRASH`**: Unhandled python exceptions.

## Dependencies

- `nl2sql.security`
- `nl2sql.engine_factory`
