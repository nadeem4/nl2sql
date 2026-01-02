# Generator Node

**Role**: The Compiler.

Translates the `PlanModel` (AST) into a dialect-specific SQL string.

## Logic

It typically uses a **Visitor Pattern** to traverse the AST.
It consults the `adapter.capabilities()` to ensure compatibility.

**Example**:

- If `adapter.supports_limit_offset` is False (e.g. older MSSQL), it simulates pagination using `TOP` and `ROW_NUMBER()`.

## Outputs

- `state.sql_draft` (str): The valid SQL string.
