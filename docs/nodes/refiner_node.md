# RefinerNode

## Purpose

The `RefinerNode` operates in the self-correction loop. When validation or execution fails, the Refiner analyzes the error, the failed plan, and the schema to generate constructive feedback (Natural Language Advice) for the Planner, enabling it to retry and fix the mistake.

## Class Reference

- **Class**: `RefinerNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/refiner/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query` (str): The original intent.
- `state.plan` (`PlanModel`): The plan that failed.
- `state.errors` (List[PipelineError]): The specific errors (e.g., "Table not found", "Execution error").
- `state.relevant_tables`: The schema context.

## Outputs

The node updates the following fields in `GraphState`:

- `state.errors` (List[PipelineError]): Appends a new error of type `PLAN_FEEDBACK` containing the LLM's advice.
- `state.reasoning` (List[Dict]): The feedback generated.

## Logic Flow

1. **Context Assembly**:
    - Dumps the `relevant_tables`, `failed_plan`, and `errors` into strings.
2. **LLM Analysis**:
    - Prompts the LLM to diagnose the failure.
    - Asks: "Given this query, this plan, and these errors, what went wrong and how should the planner fix it?"
3. **Feedback Injection**:
    - Wraps the LLM's response in a `PipelineError` (Severity WARNING).
    - This error is read by the `PlannerNode` in the next iteration as "Feedback".

## Error Handling

- **`REFINER_FAILED`**: If the refinement LLM call fails.
