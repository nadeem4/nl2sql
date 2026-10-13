# Indexing API

## Purpose
Index datasource schemas into the vector store for retrieval and grounding.

## Responsibilities
- Run schema indexing for one or all datasources.
- Clear the vector store.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/indexing_api.py`
- `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- `packages/nl2sql/src/nl2sql/indexing/vector_store.py`
- `packages/nl2sql/src/nl2sql/indexing/chunk_builder.py`
- `packages/nl2sql/src/nl2sql/indexing/enrichment_service.py`
- `packages/nl2sql/src/nl2sql/schema/store.py`

## Public Surface

### IndexingAPI.index_datasource

Source:
`packages/nl2sql/src/nl2sql/api/indexing_api.py`

Signature:
`index_datasource(datasource_id: str, enrich: bool = False) -> Dict[str, int]`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `datasource_id` | `str` | yes | Datasource ID to index. |
| `enrich` | `bool` | no | Ask the `indexing_enrichment` LLM for table and column descriptions. Off by default: it spends tokens. |

Returns:
Indexing statistics by chunk type (includes `datasource_id` and `schema_version`).

Raises:
- Adapter-specific errors for schema retrieval.
- Vector store errors during indexing. Enrichment errors are **not** raised:
  enrichment is best-effort and degrades to the unenriched snapshot.

Side Effects:
- Reads datasource schema.
- Writes schema versions to schema store.
- Writes embeddings to vector store.

Idempotency:
- Re-indexing replaces this datasource's entries: the new entries are written
  under a new build id, switched in when all are written, and the previous ones
  deleted. A failure leaves the previous entries active. Other datasources'
  entries are never touched. Raises `EmbeddingModelMismatchError` when the
  collection records a different embedding model (rebuild with
  `nl2sql index --full`).

### IndexingAPI.index_all_datasources

Signature:
`index_all_datasources(enrich: bool = False) -> Dict[str, Dict[str, int]]`

`enrich` is passed to `index_datasource` for every datasource; off by default.

Returns:
Map of datasource ID → stats; on error, value is `{"error": "<message>"}`.

Side Effects:
Indexes all registered datasources; failures are captured per datasource.

### IndexingAPI.clear_index

Signature:
`clear_index() -> None`

Side Effects:
Deletes and reinitializes the vector store collection, for every datasource.
Every question fails at the resolver until the index is rebuilt; to refresh an
index, re-index instead, which never leaves it empty.

## Execution Lifecycle
- Fetch schema via adapter.
- Only with `enrich=True`: enrich schema metadata using the `indexing_enrichment`
  LLM. Enrichment fills blanks only: a table or column comment read from the
  database is never replaced. If the client cannot be built (no API key), the
  endpoint is unreachable, or the call fails, the unenriched snapshot is used
  and indexing continues.
- Register schema snapshot and version.
- Build schema chunks and refresh vector store.
