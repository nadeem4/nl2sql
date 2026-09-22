# Indexing API

## Endpoints

### `POST /api/v1/index/{datasource_id}`

Execution flow:
- Delegates to `engine.indexing.index_datasource(...)`.

Response:
`{"success": true, "datasource_id": "...", "indexing_stats": {...}, "message": "..."}`

### `POST /api/v1/index-all`

Execution flow:
- Delegates to `engine.indexing.index_all_datasources(...)`.

### `DELETE /api/v1/index`

Execution flow:
- Delegates to `engine.indexing.clear_index()`.

### `GET /api/v1/index/status`

Execution flow:
- Delegates to `engine.index_health()` (see the
  [public facade](../core/public-facade.md#schema-index-and-retrieval)), the same
  report the playground's index panel shows.

Response (illustrative):

```json
{
  "status": "ok",
  "total": 87,
  "counts": {"schema.column": 64, "schema.datasource": 1, "schema.relationship": 11, "schema.table": 11},
  "built_at": "2026-09-22T12:35:42+00:00",
  "embedding_model": "all-MiniLM-L6-v2",
  "datasources": [
    {"datasource_id": "chinook", "entries": 87, "index_version": "20260922123542_ceed8",
     "snapshot_version": "20260922123542_ceed8", "built_at": "2026-09-22T12:35:42+00:00"}
  ],
  "problems": []
}
```

`status` is `ok`, `empty`, `stale` (the index is behind a datasource's latest
schema snapshot, or was built with another embedding model) or `missing`;
`problems` says what is wrong and how to fix it.
