# Indexing API

## Purpose
Index datasource schemas into the vector store for retrieval and grounding.

## Responsibilities
- Run schema indexing for one or all datasources.
- Clear the vector store.

## Key Modules
- `packages/core/src/nl2sql/api/indexing_api.py`
- `packages/core/src/nl2sql/indexing/orchestrator.py`
- `packages/core/src/nl2sql/indexing/vector_store.py`
- `packages/core/src/nl2sql/indexing/chunk_builder.py`
- `packages/core/src/nl2sql/indexing/enrichment_service.py`
- `packages/core/src/nl2sql/schema/store.py`

## Public Surface

### IndexingAPI.index_datasource

Source:
`packages/core/src/nl2sql/api/indexing_api.py`

Signature:
`index_datasource(datasource_id: str) -> Dict[str, int]`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `datasource_id` | `str` | yes | Datasource ID to index. |

Returns:
Indexing statistics by chunk type (includes `datasource_id` and `schema_version`).

Raises:
- Adapter-specific errors for schema retrieval.
- LLM or vector store errors during enrichment or indexing.

Side Effects:
- Reads datasource schema.
- Writes schema versions to schema store.
- Writes embeddings to vector store.

Idempotency:
- Re-indexing overwrites existing chunks for the same schema version.

### IndexingAPI.index_all_datasources

Signature:
`index_all_datasources() -> Dict[str, Dict[str, int]]`

Returns:
Map of datasource ID → stats; on error, value is `{"error": "<message>"}`.

Side Effects:
Indexes all registered datasources; failures are captured per datasource.

### IndexingAPI.clear_index

Signature:
`clear_index() -> None`

Side Effects:
Deletes and reinitializes the vector store collection.

## Execution Lifecycle
- Fetch schema via adapter.
- Enrich schema metadata using `indexing_enrichment` LLM.
- Register schema snapshot and version.
- Build schema chunks and refresh vector store.
