# Adding New Chunk Types

Chunking is the backbone of retrieval. This guide explains how to add new schema chunk types.

## 1. Add a new chunk model

Define a new chunk class in `nl2sql.indexing.models`:

- Inherit from `BaseChunk`.
- Provide a **stable, deterministic ID**.
- Implement `get_page_content()` and metadata via `get_metadata()`.

## 2. Extend the chunk builder

Update `SchemaChunkBuilder` to emit the new chunk type:

- Add a `_build_*_chunks()` method.
- Include it in `build()` alongside existing chunk builders.

## 3. Update retrieval filters

If your chunk type should be retrievable:

- Add it to the appropriate `Retriever.retrieve_*` filters. Its metadata must be scalars (an index entry holds nothing else).
- Update `SchemaRetrieverNode` logic if new chunk types influence planning.

## 4. Update planner/validator (if required)

If the chunk type carries new semantics, ensure:

- `ASTPlannerNode` prompt includes the relevant context.
- `LogicalValidatorNode` can enforce any new constraints.

## Source references

- Chunk models: `packages/nl2sql/src/nl2sql/indexing/models.py`
- Chunk builder: `packages/nl2sql/src/nl2sql/indexing/chunk_builder.py`
- Retrieval: `packages/nl2sql/src/nl2sql/indexing/retrieval.py`
- Schema retriever: `packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
