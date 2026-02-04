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
- Returns placeholder status from `IndexingService.get_index_status(...)`.
