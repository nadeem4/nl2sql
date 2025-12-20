# DirectSQLNode

## Purpose

The `DirectSQLNode` is the engine of the **Fast Lane**. It is designed to handle low-complexity queries (Intent: `TABULAR` or `KPI`) where generating a formal execution plan is unnecessary overhead.

## Components

- **`LLM`**: A lightweight LLM call to translate natural language to SQL.
- **`DatasourceRegistry`**: To fetch the schema definition (DDL) for the prompt.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The canonicalized natural language query.
- `state.selected_datasource_id`: The target database.
- `state.schema_info`: The schema definitions retrieved by the `SchemaNode`.

## Outputs

The node updates the following fields in `GraphState`:

- `state.sql_draft`: The generated SQL query.
- `state.reasoning`: Log entry.

## Logic Flow

1. **Schema Context**: Reads `state.schema_info` to understand tables and columns.
2. **Prompting**: Uses a specialized "Direct Translation" prompt that encourages standard SQL generation without chain-of-thought planning.
3. **Generation**: Produces the `sql_draft` directly.

## Error Handling

- **`GENERATION_FAILED`**: If the LLM produces invalid output.
- **Note**: Validation is skipped in the Fast Lane, so errors are typically caught by the `ExecutorNode` during runtime.

## Dependencies

- `nl2sql.llm_registry`
