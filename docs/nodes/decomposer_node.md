# DecomposerNode

## Purpose

The `DecomposerNode` acts as the **Router** and **Orchestrator** of the pipeline. It parses the canonicalized user query and breaks it down into independent sub-queries, each targeted at a specific datasource. This is crucial for handling multi-datasource requests or complex analytical questions.

## Components

- **`LLM`**: Used to perform the decomposition and reasoning.
- **`OrchestratorVectorStore`**: Provides relevant schema context for the LLM to make informed routing decisions.
- **`DatasourceRegistry`**: Provides metadata (descriptions) about available data sources.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The **canonicalized** natural language query (from IntentNode).
- `state.enriched_terms`: List of specific keywords/entities to aid vector search context retrieval.
- `state.selected_datasource_id`: (Optional) If set, the node acts in "Pass-through" mode.

## Outputs

The node updates the following fields in `GraphState`:

- `state.sub_queries`: A list of `SubQuery` objects, each containing:
  - `datasource_id`: Target database.
  - `query`: The specific question for that database.
  - `candidate_tables`: (Optional) Pre-identified tables.
- `state.reasoning`: Log entry explaining the decomposition logic.
- `state.errors`: Appends `PipelineError` if orchestration fails.

## Logic Flow

1. **Direct Execution Check**:
    - If `state.selected_datasource_id` is already present, it creates a single `SubQuery` targeting that datasource.
2. **Context Retrieval**:
    - Uses `state.user_query` + `state.enriched_terms` to query the `VectorStore`.
3. **LLM Decomposition**:
    - Prompts the LLM with the query, available datasources, and retrieved schema context.
    - The LLM generates a plan (`DecomposerResponse`) consisting of one or more sub-queries.
4. **State Update**: The resulting `sub_queries` are stored in the state, which triggers parallel execution branches.

## Error Handling

- **`ORCHESTRATOR_CRASH`**: Critical failure in the decomposition process (e.g., LLM error, context retrieval failure).

## Dependencies

- `nl2sql.nodes.decomposer.schemas.DecomposerResponse`
