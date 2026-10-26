# Indexing API

## Endpoints

`POST /api/v1/index/{datasource_id}`, `POST /api/v1/index-all` and
`DELETE /api/v1/index` are admin only (see [Admin routes](index.md#admin-routes));
`GET /api/v1/index/status` is open.

### `POST /api/v1/index/{datasource_id}`

Execution flow:
- Delegates to `engine.index.build([datasource_id])`: the new entries are
  written beside the live ones and switched in when all are written. No
  model is called: a build embeds the schema, the glossary and the verified
  queries.

Response:
`{"success": true, "datasource_id": "...", "indexing_stats": {...}, "message": "..."}`

Errors:
- A datasource that fails to index (unknown, unavailable, unreadable) ->
  `HTTP 500` naming the reason. It keeps its previous entries.

### `POST /api/v1/index-all`

Execution flow:
- Delegates to `engine.index.build()`, every configured datasource.

Response:
`{"success": <every datasource indexed>, "indexing_results": {"stats": [...], "empty": [...], "errors": [...]}, "message": "..."}`.
A datasource that fails is in `errors` (`datasource_id`, `error`) and keeps
its previous entries.

### `DELETE /api/v1/index`

Execution flow:
- Delegates to `engine.index.clear()`.

### `GET /api/v1/index/status`

Execution flow:
- Delegates to `engine.index.health()` (see
  [Indexing API → index.health](../core/indexing.md#indexhealth)), the same
  report the playground's index panel shows.

Response (illustrative):

```json
{
  "status": "ok",
  "total": 162,
  "counts": {"schema.column": 121, "schema.datasource": 3, "schema.relationship": 18, "schema.table": 20},
  "built_at": "2026-09-22T12:35:42+00:00",
  "embedding_model": "local/all-MiniLM-L6-v2",
  "namespace": "nl2sql__local-all-minilm-l6-v2__384",
  "datasources": [
    {"datasource_id": "chinook", "entries": 87, "index_version": "20260922123542_ceed8",
     "built_at": "2026-09-22T12:35:42+00:00"},
    {"datasource_id": "support", "entries": 36, "index_version": "...",
     "built_at": "2026-09-22T12:35:42+00:00"},
    {"datasource_id": "webanalytics", "entries": 39, "index_version": "...",
     "built_at": "2026-09-22T12:35:42+00:00"}
  ],
  "problems": []
}
```

`status` is `ok`, `empty`, `stale` (a datasource has no live build, or its
build's snapshot is gone) or `missing` (the configured embedding model has no
index yet);
`problems` says what is wrong and how to fix it.
