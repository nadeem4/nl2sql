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

1. **Context Preparation**: Formats all `intermediate_results` into a single text block for the LLM. Includes any error messages to provide transparency about partial failures.
2. **LLM Invocation**: Calls the LLM with the context to produce an `AggregatedResponse`.
3. **Formatting**:
    - Extracts the `summary` and `content`.
    - Formats the output based on `format_type` (table vs list vs text).
    - Constructs the final markdown string.

## Error Handling

- **`AGGREGATOR_FAILED`**: If the LLM output is malformed or processing fails.

## Dependencies

- `nl2sql.nodes.aggregator.schemas.AggregatedResponse`
