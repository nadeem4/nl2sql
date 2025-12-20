# SchemaNode

## Purpose

The `SchemaNode` is responsible for retrieving and formatting the database schema (tables, columns, foreign keys) that is relevant to the user's query. It acts as the context provider for the subsequent planning and generation steps.

## Components

- **`SafeInspector`**: A wrapper around SQLAlchemy's inspector to handle database introspection errors gracefully.
- **`OrchestratorVectorStore`**: optional integration to perform semantic search for relevant tables.

## Inputs

- `state.selected_datasource_id`: The ID of the database to query.
- `state.user_query`: The natural language query from the user.
- `state.candidate_tables` (Optional): A pre-selected list of tables from the `DecomposerNode`.
- `state.enriched_terms` (Optional): Keywords and entities used to aid in table scoring.

## Outputs

The node updates the following fields in `GraphState`:

- `state.schema_info`: A structured object containing `TableInfo` for all retrieved tables.
- `state.reasoning`: Log entry describing how many tables were retrieved.
- `state.system_events`: Emits `DRIFT_DETECTED` if the semantic fallback mechanism was triggered.
- `state.errors`: Appends `PipelineError` if retrieval fails critically.

## Logic Flow

1. **Candidate Selection**:
    - Checks if `state.candidate_tables` are provided (Pre-routed).
    - If not, attempts to retrieve candidates from `VectorStore` (Vector Search).
    - **Semantic Fallback**: If Vector Search returns nothing (or no index exists), it fetches *all* live table names from the DB and performs on-demand cosine similarity against the user query to find the top matches.
    - If Fallback is used, `drift_detected` flag is set.

2. **Table Expansion**:
    - Verifies that selected candidates actually exist in the database (using `SafeInspector`).
    - Expands the list to include **Related Tables** by following Foreign Keys (depth-1 expansion).

3. **Schema Extraction**:
    - For every table in the final list, inspects:
        - **Columns**: Name and Type.
        - **Foreign Keys**: Constrained column, referred table, referred column.
    - Assigns a short alias (e.g., `t1`, `t2`) to each table for concise SQL generation.

4. **Event Handling**:
    - If `drift_detected` is True, appends `DRIFT_DETECTED` to `state.system_events`.

## Error Handling

- **`SCHEMA_RETRIEVAL_FAILED`**: Critical error if the database connection fails or introspection throws an unhandled exception.
- **`DRIFT_DETECTED`**: Warning signal indicating that the Vector Store is out of sync with the Database.

## Dependencies

- `nl2sql.datasource_registry.DatasourceRegistry`
- `nl2sql.vector_store.OrchestratorVectorStore`
- `sqlalchemy.engine.Engine`
