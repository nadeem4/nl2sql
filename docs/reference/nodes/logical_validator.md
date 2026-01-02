# Logical Validator Node

**Role**: The Gatekeeper (Policy & Logic).

Operates on the AST (`PlanModel`) before any SQL is generated.

## Checks

1. **Static Analysis**:
    - Do the columns exist in `relevant_tables`?
    - Are table aliases used consistently?
    - Are JOIN conditions referencing valid keys?

2. **Policy Enforcement (RBAC)**:
    - Checks `state.user_context`.
    - Ensures the user has `READ` access to all referenced tables.
    - **Constraint**: If `user_context.allowed_tables` is set, any table outside this list triggers a `SECURITY_VIOLATION`.

## Error Handling

Returns `LogicalError` to the `Refiner` if validaton fails.
