# DecomposerNode

## Purpose

The `DecomposerNode` acts as the **Router** and **Orchestrator** of the pipeline. It parses the user's complex natural language query and breaks it down into independent sub-queries, each targeted at a specific datasource. This is crucial for handling multi-datasource requests or complex analytical questions.

## Components

- **`LLM`**: Used to perform the decomposition and reasoning.
- **`OrchestratorVectorStore`**: Provides relevant schema context for the LLM to make informed routing decisions.
- **`DatasourceRegistry`**: Provides metadata (descriptions) about available data sources.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The original natural language query.
- `state.selected_datasource_id`: (Optional) If set, the node acts in "Pass-through" mode, targeting only this datasource.

## Outputs

The node updates the following fields in `GraphState`:

- `state.sub_queries`: A list of `SubQuery` objects, each containing:
  - `datasource_id`: Target database.
  - `query`: The specific question for that database.
  - `candidate_tables`: (Optional) Pre-identified tables.
- `state.reasoning`: Log entry explaining the decomposition logic.
- `state.errors`: Appends `PipelineError` if orchestration fails.

## Logic Flow

1. **Canonicalization**: Standadizes the user query (e.g., resolving temporal references).
2. **Direct Execution Check**:
    - If `state.selected_datasource_id` is already present (e.g., from UI selection), it bypasses detailed decomposition.
    - It creates a single `SubQuery` targeting that datasource.
    - It optionally queries the `VectorStore` to pre-fill `candidate_tables`.
3. **Context Retrieval**:
    - If no direct selection, it queries the `VectorStore` (Level 1/2 Search) to find relevant tables and schemas across *all* datasources.
4. **LLM Decomposition**:
    - Prompts the LLM with the query, available datasources, and retrieved schema context.
    - The LLM generates a plan (`DecomposerResponse`) consisting of one or more sub-queries.
5. **State Update**: The resulting `sub_queries` are stored in the state, which triggers parallel execution branches.

## Error Handling

- **`ORCHESTRATOR_CRASH`**: Critical failure in the decomposition process (e.g., LLM error, context retrieval failure).

## Dependencies

- `nl2sql.agents.canonicalize_query`
- `nl2sql.nodes.decomposer.schemas.DecomposerResponse`
