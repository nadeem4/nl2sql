# AggregatorNode

## Purpose

The `AggregatorNode` combines results from the execution phase and prepares the final response. It implements a "Fast Path" for direct data streaming and a "Slow Path" for LLM-based summarization or answer synthesis.

## Class Reference

- **Class**: `AggregatorNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/aggregator/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query` (str): The user's question.
- `state.intermediate_results` (List): Results from the executor(s).
- `state.output_mode` (str): "data" (Fast Path) or "summary"/"verbose" (Slow Path).
- `state.errors` (List[PipelineError]): Any errors to include in the summary.

## Outputs

The node updates the following fields in `GraphState`:

- `state.final_answer` (Any): The final text entry or data payload.
- `state.reasoning` (List[Dict]): Log of which path was taken.

## Logic Flow

1. **Fast Path Check**:
    - If there is exactly one result, no errors, and `output_mode` is "data":
    - Returns the raw data directly.
2. **Slow Path (LLM Aggregation)**:
    - Formats all `intermediate_results` (and errors) into a string.
    - Prompts the LLM to synthesize an answer to the `user_query` using the provided data.
    - Formats the LLM output (Table/List/Text).
    - Returns the generated summary.

## Error Handling

- **`AGGREGATOR_FAILED`**: If the LLM summarization fails.
