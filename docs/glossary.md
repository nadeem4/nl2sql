# Glossary

## Core concepts

- **ArtifactRef**: Metadata reference to persisted query results (URI, backend, schema_version).
- **Chunk**: Typed, vector-indexed schema slice used for retrieval (datasource, table, column, relationship).
- **ExecutionDAG**: Deterministic logical DAG of scan/combine/post operations.
- **GraphState**: Shared state across the control graph pipeline.
- **SchemaContract**: Canonical schema structure for a datasource (tables, columns, foreign keys).
- **SchemaMetadata**: Descriptive metadata and statistics for schema elements.
- **SchemaSnapshot**: Pair of `SchemaContract` + `SchemaMetadata`.
- **SubQuery**: Decomposed, datasource-scoped query intent from the decomposer.
- **Subgraph**: LangGraph subgraph that handles a specific execution capability (e.g., SQL agent).
- **ResultFrame**: Adapter response containing rows/columns, row_count, and error metadata.
- **VectorStore**: Chroma-backed vector index for schema chunks.
- **RBAC**: Role-based access control enforcing datasource/table permissions.
