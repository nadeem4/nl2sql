# Executor Node

**Role**: The Driver.

Executes the validated SQL and returns results.

## Logic

1. Calls `adapter.execute(sql)`.
2. Catches low-level database exceptions (timeouts, connection issues).
3. Formats the result into `ExecutionModel`.

## Outputs

- `state.execution.rows`: List of result rows.
- `state.execution.columns`: Column headers.
