# Physical Validator Node

**Role**: The Field Tester.

Validates the generated SQL against the actual database engine *without* executing it fully.

## Checks

1. **Dry Run**:
    - Executes `adapter.dry_run(sql)` (usually `EXPLAIN` or a rolled-back transaction).
    - Catches runtime errors like "Ambiguous column name" or "Function X does not exist".

2. **Cost Estimation**:
    - Executes `adapter.cost_estimate(sql)`.
    - **Guardrail**: If `estimated_rows > ROW_LIMIT`, it flags a performance warning or blocks execution.

## Error Handling

Returns `PhysicalError` to the `Refiner` if validation fails.
