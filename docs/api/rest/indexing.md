# Indexing API

Indexing a datasource is a [job](jobs.md): it outlives its request. The routes here read the index, clear it, check
it for drift and inspect retrieval. `DELETE /api/v1/index` and `GET /api/v1/index/check` are admin only (see
[Admin routes](index.md#admin-routes)); `GET /api/v1/index` and the inspector are open to any caller.

## Start an index build

`POST /api/v1/jobs/index` (admin, `202`). Body: `{"datasource_ids": ["chinook"]}`; leave `datasource_ids` out to index
every configured datasource. Unknown keys are a `422`.

It delegates to `engine.index.build(datasource_ids, on_datasource=..., cancellation_token=...)`: the new entries are
written beside the live ones and switched in when all are written. No model is called: a build embeds the schema, the
glossary and the verified queries. Progress is one step per datasource (`done` of `total`); a datasource that failed is
the step `<id> (failed)` and keeps its previous entries. Its reason goes to the server log, never the job.

The job's `result` (owner only) is `{"ok": bool, "stats": [...], "empty": [...], "failed": ["<id>", ...]}`.

## `GET /api/v1/index`

`engine.index.health()` (see [Indexing API](../core/indexing.md#indexhealth)), the report the index panel shows.

```json
{
  "status": "ok",
  "total": 162,
  "counts": {"schema.column": 121, "schema.table": 20},
  "built_at": "2026-09-22T12:35:42+00:00",
  "embedding_model": "local/all-MiniLM-L6-v2",
  "namespace": "nl2sql__local-all-minilm-l6-v2__384",
  "datasources": [
    {"datasource_id": "chinook", "entries": 87, "index_version": "20260922123542_ceed8",
     "built_at": "2026-09-22T12:35:42+00:00"}
  ],
  "problems": []
}
```

`status` is `ok`, `empty`, `stale` (a datasource has no live build, or its build's snapshot is gone) or `missing` (the
configured embedding model has no index yet); `problems` says what is wrong and how to fix it.

## `DELETE /api/v1/index`

Admin. `engine.index.clear()`; answers `{"success": true, "message": "..."}`.

## `GET /api/v1/index/check`

Admin. `engine.index.check(datasource_ids, user_context=caller)` compares each datasource's live schema with what
was indexed. Repeat `datasource_id` to name some; leave it out for every datasource. Response: a list of
`DriftReport` (`datasource_id`, `status`, `live_version`, `checked_at` and what changed). An unknown datasource is a
`HTTP 404`.

## The retrieval inspector

`GET /api/v1/index/inspect` returns the defaults (`k`, `lambda_mult`, `fetch_multiplier`, `types`).
`POST /api/v1/index/inspect` takes `{"query": "...", "k": 8, "lambda_mult": null, "types": null, "datasource_id": null}`
(`query` required, `k` at least 1, unknown keys a `422`) and returns the candidate `pool`, the `picks` and what was
`dropped`, for the caller's scope.
