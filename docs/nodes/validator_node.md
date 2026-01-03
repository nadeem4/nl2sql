# LogicalValidatorNode

## Purpose

The `LogicalValidatorNode` validates the generated AST (`PlanModel`) *before* any SQL is generated. It performs static analysis to ensure the plan structure is valid (e.g., no duplicate aliases) and that all referenced tables and columns actually exist in the schema. It also enforces access control policies.

## Class Reference

- **Class**: `LogicalValidatorNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/validator/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.plan` (`PlanModel`): The plan to validate.
- `state.relevant_tables` (List[Table]): The schema context to check against.
- `state.user_context` (Dict): Used for policy validation (`allowed_tables`).

## Outputs

The node updates the following fields in `GraphState`:

- `state.errors` (List[PipelineError]): Appends error validation failures found.
  - `TABLE_NOT_FOUND`: Referenced table does not exist in `relevant_tables`.
  - `INVALID_PLAN_STRUCTURE`: Malformed AST (e.g., non-contiguous ordinals).
  - `SECURITY_VIOLATION`: Reference to unauthorized table.

## Logic Flow

1. **Duplicate Alias Check**: Ensures all table aliases in the plan are unique.
2. **Schema Verification (`_build_alias_map`)**:
    - Iterates through `plan.tables`.
    - Verifies each table exists in `state.relevant_tables`.
    - Maps aliases to valid columns for downstream checks.
3. **Policy Validation**:
    - Checks if the user is authorized to access the referenced tables based on `state.user_context`.
4. **Static Validation**:
    - (Implied) Additional checks on the structure of the AST.

## Error Handling

- **`TABLE_NOT_FOUND`**: If the plan invents a table name.
- **`SECURITY_VIOLATION`**: If a table is restricted by policy.
