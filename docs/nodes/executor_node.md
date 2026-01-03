# ExecutorNode

## Purpose

The `ExecutorNode` is responsible for executing the generated SQL query against the target datasource. It handles connection management via the `DatasourceRegistry` adapters, safeguards against massive result sets, and formats the output.

## Class Reference

- **Class**: `ExecutorNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/executor/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.sql_draft` (str): The SQL query to execute.
- `state.selected_datasource_id` (str): The target database ID.

## Outputs

The node updates the following fields in `GraphState`:

- `state.execution` (`ExecutionModel`): The result of the query.
  - `columns` (List[str]): Column names.
  - `rows` (List[Dict]): The data returned.
  - `row_count` (int): Number of rows.
- `state.errors` (List[PipelineError]): Errors during execution.

## Logic Flow

1. **Validation**: Ensures `sql_draft` and `datasource_id` are present.
2. **Adapter Retrieval**: Fetches the correct adapter (e.g., PostgresAdapter) from the registry.
3. **Cost Estimation (Safeguard)**:
    - If supported by the adapter, estimates the query cost.
    - If the estimated row count exceeds `SAFEGUARD_ROW_LIMIT` (10,000), aborts execution and raises `SAFEGUARD_VIOLATION`.
4. **Execution**:
    - Runs `adapter.execute(sql)`.
    - Captures the result set.
5. **Formatting**: Converts the results into the standard `ExecutionModel`.

## Error Handling

- **`SAFEGUARD_VIOLATION`**: If the query is predicted to return too many rows.
- **`DB_EXECUTION_ERROR`**: If the database raises an exception (e.g., timeout, syntax error not caught by validator).
