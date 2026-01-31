# ADR-003: Schema Chunking Strategy

## Status

Accepted (implemented in `SchemaChunkBuilder` and `VectorStore`).

## Context

Full-schema injection into LLM prompts is brittle and expensive. Retrieval needs to be **semantically structured** so that:

- Datasource routing is reliable.
- Schema grounding is precise.
- Planning context is scoped to relevant tables and columns.

## Decision

Use **typed schema chunks** with staged retrieval:

- `schema.datasource` for datasource routing and grounding.
- `schema.table` for table-level context and primary keys.
- `schema.column` for column semantics and statistics.
- `schema.relationship` for explicit join hints.

Retrieval is staged in `SchemaRetrieverNode`:

1. `retrieve_schema_context()` (tables/metrics)
2. fallback to `retrieve_column_candidates()` if no tables found
3. `retrieve_planning_context()` for columns/relationships of selected tables

## Consequences

- Reduces LLM context to schema slices relevant to the query.
- Preserves authoritative schema by resolving final context from `SchemaStore`.
- Enables deterministic and explainable retrieval behavior.

## Source references

- Chunk models: `packages/core/src/nl2sql/indexing/models.py`
- Chunk builder: `packages/core/src/nl2sql/indexing/chunk_builder.py`
- Retrieval: `packages/core/src/nl2sql/indexing/vector_store.py`
- Schema retriever: `packages/core/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
