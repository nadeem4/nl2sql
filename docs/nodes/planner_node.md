# PlannerNode

## Purpose

The `PlannerNode` is the cognitive core of the SQL generation process. It synthesizes the user's intent and the retrieved schema (tables, columns) to create a structured, dialect-agnostic "Execution Plan" (`PlanModel`). Unlike a simple text-to-SQL prompt, this node generates a deterministic Abstract Syntax Tree (AST), ensuring strict adherence to the schema and enabling logical validation before any SQL is written.

## Class Reference

- **Class**: `PlannerNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/planner/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query` (str): The original natural language question.
- `state.relevant_tables` (List[Table]): The list of schema definitions (tables/columns) found relevant by the Decomposer.
- `state.semantic_analysis` (SemanticAnalysisResponse): Enriched context (keywords, synonyms) to guide planning.
- `state.errors` (List[PipelineError]): Existing errors (if in a re-planning loop), used to provide feedback to the LLM.
- `state.selected_datasource_id` (str): ID of the target database, used to fetch dialect-specific settings (e.g., date formats).

## Outputs

The node updates the following fields in `GraphState`:

- `state.plan` (`PlanModel`): The comprehensive execution plan containing:
  - **tables**: List of `TableNode` (name, alias, schema).
  - **joins**: List of `JoinNode` (right table, condition, join type).
  - **select_items**: List of `SelectItemNode` (expression, alias).
  - **where**: Root `Expr` node for filtering.
  - **group_by**: List of `GroupByNode`.
  - **having**: Root `Expr` node for post-aggregation filtering.
  - **order_by**: List of `OrderByNode`.
  - **limit**: Integer row limit.
- `state.reasoning` (List[Dict]): Log entry explaining the planning decisions.
- `state.errors` (List[PipelineError]): Appends `MISSING_LLM` or `PLANNING_FAILURE` if generation fails.

## Logic Flow

1. **Initialization**: Checks if the LLM is provided; otherwise returns a critical error.
2. **Context Assembly**:
    - Serializes `state.relevant_tables` into a schema string.
    - Formats previous `state.errors` into a feedback string (for self-correction).
    - Retrieves the `date_format` from the selected datasource profile.
    - includes `state.semantic_analysis` context.
3. **LLM Invocation**:
    - Prompts the LLM with the query, schema, examples, and feedback.
    - Expects a JSON response conforming to `PlanModel`.
4. **Post-Processing**:
    - Validates and parses the JSON into the `PlanModel` Pydantic object.
    - Updates `state.plan` and logs reasoning.

## Error Handling

- **`MISSING_LLM`**: Raised if the node is initialized without a language model.
- **`PLANNING_FAILURE`**: Raised if the LLM output is malformed or cannot be parsed into `PlanModel`.
