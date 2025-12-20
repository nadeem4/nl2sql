# AggregatorNode

## Purpose

The `AggregatorNode` is responsible for consolidating results from multiple parallel execution branches (triggered by the `DecomposerNode`) into a single, coherent response. It handles data merging, de-duplication, and formatting (e.g., combining two partial tables into one).

## Components

- **`LLM`**: Used to synthesize the final answer and decide the best presentation format.
- **`AggregatedResponse`**: Structured output schema.

## Inputs

The node reads the following fields from `GraphState`:

- `state.intermediate_results`: A list of results collected from all parallel branches (each containing execution data or errors).
- `state.user_query`: The original global query.
- `state.errors`: List of errors encountered in the branches (to report partial failures).

## Outputs

The node updates the following fields in `GraphState`:

- `state.final_answer`: A markdown-formatted string containing the summary and combined data.
- `state.reasoning`: Log entry describing the chosen format.
- `state.errors`: Appends `PipelineError` if aggregation fails.

## Logic Flow

1. **Fast Path Check**:
    - Checks if `state.response_type` is `TABULAR` or `KPI`.
    - If true, and there is a single successful result, returns `final_answer=None`.
    - This signals the Presentation Layer (CLI) to display the raw `ExecutionModel` directly.
2. **Slow Path (LLM)**:
    - If `state.response_type` is `SUMMARY` or multiple results exist.
    - Formats all `intermediate_results` into a single text block.
    - Invokes the LLM to synthesize an answer (`AggregatedResponse`).
3. **Formatting**:
    - Constructs a markdown string combining the summary and content.

## Error Handling

- **`AGGREGATOR_FAILED`**: If the LLM output is malformed or processing fails.

## Dependencies

- `nl2sql.nodes.aggregator.schemas.AggregatedResponse`
