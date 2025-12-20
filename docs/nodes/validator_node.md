# ValidatorNode

## Purpose

The `ValidatorNode` acts as an automated quality assurance check between the `PlannerNode` and the `GeneratorNode`. It performs static analysis on the proposed `PlanModel` to catch logical errors, hallucinations (references to non-existent columns), and potential runtime issues (e.g., date format mismatch) *before* any SQL is generated or executed.

## Components

- **`DatasourceRegistry`**: To fetch profile settings (like date formats) for type validation.
- **`SchemaInfo`**: used to verify table and column existence.

## Inputs

The node reads the following fields from `GraphState`:

- `state.plan`: The execution plan to validate.
- `state.schema_info`: The source of truth for database structure.
- `state.selected_datasource_id`: Context for data type rules.

## Outputs

The node updates the following fields in `GraphState`:

- `state.errors`: Appends `PipelineError` objects for any violations found.
  - If errors are present, the pipeline usually loops back to the Planner for correction.
- `state.reasoning`: Log entry summarizing validation pass/fail status.

## Logic Flow

1. **Security Check**: Verifies `query_type` is "READ".
2. **Schema Verification**:
    - Checks that all tables in the plan exist in `schema_info`.
    - Checks that all column references (SELECT, WHERE, JOIN keys) exist in the mapped tables.
3. **Logical Validation**:
    - **Joins**: Ensures joined tables are part of the plan and have ON conditions.
    - **Aggregations**: Enforces SQL rules (e.g., non-aggregated columns in SELECT must be in GROUP BY).
    - **Aliases**: Ensures aliases are used correctly.
4. **Type & Format Validation**:
    - Checks literal values in Filters/Having clauses against column types (e.g., ensuring a string matches `ISO 8601` if compared to a DATE column).

## Error Handling

- **`MISSING_PLAN`**: Blocking error.
- **`SECURITY_VIOLATION`**: If WRITE/DDL intent slipped through.
- **`TABLE_NOT_FOUND` / `COLUMN_NOT_FOUND`**: Hallucinations.
- **`INVALID_DATE_FORMAT` / `INVALID_NUMERIC_VALUE`**: Type mismatches.
- **`MISSING_GROUP_BY`**: SQL logic error.

## Dependencies

- `nl2sql.nodes.planner.schemas.PlanModel`
