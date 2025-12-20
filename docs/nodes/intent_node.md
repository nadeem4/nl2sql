# IntentNode

## Purpose

The `IntentNode` acts as the system's entry point, analyzing the user's natural language query to determine the best response strategy. It performs three key functions:

1. **Classification**: Determines if the response should be a raw table (`TABULAR`), a single metric (`KPI`), or an analytical summary (`SUMMARY`).
2. **Canonicalization**: Rewrites the query into a standardized form (e.g., "show me guys" -> "List operators").
3. **Enrichment**: Extracts entities and keywords to aid downstream vector search.

## Components

- **`LLM (Language Model)`**: Uses a specialized prompt (`INTENT_PROMPT`) to analyze intent.
- **`IntentResponse`**: Structured output schema defining `response_type`, `canonical_query`, etc.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The original natural language query from the user.

## Outputs

The node updates the following fields in `GraphState`:

- `state.user_query`: Updated to the **canonicalized** query.
- `state.response_type`: One of `tabular`, `kpi`, `summary`.
- `state.enriched_terms`: A list of extracted keywords, entities, and synonyms.
- `state.reasoning`: Log entry summarizing the decision.

## Logic Flow

1. **Analyze**: Invokes the LLM with the user query.
2. **Classify**: specific prompt logic determines if the user wants raw data (fast path) or analysis (slow path).
3. **Update State**: results are written to `GraphState` for the `DecomposerNode` and `AggregatorNode` to consume.

## Error Handling

- **Fallback**: If classification fails, it defaults to `response_type="tabular"` to ensure the pipeline continues.
- **Logging**: Errors are logged to `state.errors` and the standard logger.
