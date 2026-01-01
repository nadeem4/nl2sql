# PlannerNode

## Purpose

The `PlannerNode` is the cognitive core of the SQL generation process. It synthesizes the user's intent and the retrieved schema (tables, columns) to create a structured, dialect-agnostic "Execution Plan". It is responsible for figuring out *how* to answer the query logically (joins, filters, aggregations) before any SQL is written.

## Components

- **`LLM`**: The language model reasoning engine.
- **`PlanModel`**: The output schema defining the abstract query structure.
- **`PLANNER_PROMPT`**: Guides the LLM to map natural language to the inputs.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The original question.
- `state.relevant_tables`: The list of available table schemas (from DecomposerNode).
- `state.semantic_analysis`: Enriched context (keywords/entities) to guide planning.
- `state.errors` (Optional): Previous errors, if re-planning is triggered.
- `state.selected_datasource_id`: ID of the target database (for date format context).

## Outputs

The node updates the following fields in `GraphState`:

- `state.plan`: A `PlanModel` dictionary containing:
  - `tables`: List of tables to query.
  - `joins`: Join conditions between tables.
  - `select_columns`: Columns to retrieve.
  - `filters`: Where clauses.
  - `group_by`, `having`, `order_by`: Aggregation and sorting logic.
  - `limit`: Row limit.
- `state.reasoning`: Log entry explaining the planning decisions.
- `state.errors`: Appends `PipelineError` if LLM fails usage.

## Logic Flow

1. **Context Assembly**:
    - Serializes `schema_info` and `intent` into JSON context for the prompt.
    - If `state.errors` exist (Re-planning loop), formats them as "Feedback" to guide the LLM to fix mistakes.
    - Retrieves dialect-specific `date_format` (e.g., ISO 8601) to help the LLM generate correct date strings.
2. **LLM Invocation**: Calls the LLM to generate the `PlanModel`.
3. **Post-Processing**:
    - Ensures `query_type` matches the Intent.
    - Updates the state with the plan.

## Error Handling

- **`MISSING_LLM`**: If initialized without an LLM.
- **`PLANNING_FAILURE`**: If the LLM generates invalid JSON or fails to adhere to the schema.

## Dependencies

- `nl2sql.nodes.planner.schemas.PlanModel`
