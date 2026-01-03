# GeneratorNode

## Purpose

The `GeneratorNode` is the compiler of the pipeline. It takes the abstract execution plan (`PlanModel`) produced by the Planner and generates a valid, dialect-specific SQL string. It uses `sqlglot` to transpile the internal AST into the target SQL dialect (e.g., PostgreSQL, T-SQL, MySQL), enforcing syntactic correctness.

## Class Reference

- **Class**: `GeneratorNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/generator/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.plan` (`PlanModel`): The logical plan to compile.
- `state.selected_datasource_id` (str): The ID of the target database, used to determine the SQL dialect.

## Outputs

The node updates the following fields in `GraphState`:

- `state.sql_draft` (str): The generated SQL query string.
- `state.reasoning` (List[Dict]): Logs the generated SQL.
- `state.errors` (List[PipelineError]): `SQL_GEN_FAILED` if compilation errors occur.

## Logic Flow

1. **Validation**: Checks if a plan and a datasource ID are present in the state.
2. **Profile Lookup**: Fetches the `dialect` (e.g., "postgres", "tsql") and default `row_limit` from the datasource registry.
3. **AST Transformation (`SqlVisitor`)**:
    - The node uses a `SqlVisitor` class to traverse the `PlanModel` (Expr tree).
    - It builds a corresponding `sqlglot` Expression tree.
    - This visitor handles literals, columns, functions, binary/unary operations, and case statements.
4. **SQL Synthesis**:
    - Constructs the top-level `SELECT` statement using `sqlglot` builders.
    - Applies transformations for `SELECT`, `FROM` (Tables), `JOIN`, `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`, and `LIMIT`.
    - Handles dialect-specific nuances (e.g., quoting identifiers, function names) via `sqlglot.transpile` mechanisms (implicit in `.sql(dialect=...)`).
5. **Output**: Returns the final SQL string.

## Error Handling

- **`SQL_GEN_FAILED`**: Raised if the visitor encounters unknown expression types or if `sqlglot` fails to generate the string.
