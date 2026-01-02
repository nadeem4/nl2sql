# Planner Node

**Role**: The Architect.

The Planner is responsible for translating the user's ambiguous intent into a rigorous, schematic plan. It does *not* write SQL. It writes an AST.

## Inputs

- `user_query` (str)
- `relevant_tables` (List[Table])

## Outputs

- `plan` (`PlanModel`)

## Logic

1. Analyzes the query complexity.
2. Maps natural language terms to specific schema columns.
3. Constructs a `PlanModel` (AST) containing:
    - `statement_type`: SELECT, INSERT, etc.
    - `select_items`: Columns to retrieve.
    - `filters`: Where clauses.
    - `joins`: Relationships between tables.
