# Health API

Source: `packages/api/src/nl2sql_api/routes/health.py`. `/health` and `/ready` need no caller, nor does `/meta` (a caller adds fields), and `/about` needs one.

## Endpoints

### `GET /api/v1/health`

Liveness. Returns `{"status": "ok", "version": {"engine": "<nl2sql-engine>", "api": "<nl2sql-api>"}}`.

### `GET /api/v1/ready`

Readiness, from the engine's own `check()` (kept for 10 seconds, so a poll every few seconds does not connect to every
datasource each time). Ready means every required datasource and at least one datasource is up, every agent's key
resolves (unless `api.request_llm` is on, or every agent is on the `replay` provider) and the index is `ok` or `stale`.
`200 {"status": "ready"}` or `503 {"status": "not_ready"}`, to everyone. A caller who is an admin also gets `problems`:
each failing datasource and its stage, each agent whose key does not resolve and the index status, never a secret. The
API starts even when a required datasource cannot connect; this route says so.

### `GET /api/v1/meta`

Without a caller: `{"mode": "demo" | "your_data" | "hosted", "auth": "api_keys" | "proxy" | "static" | "none",
"request_llm": false}`, what a page needs before it has an identity. With a caller, also `datasources`, each one the
caller may reach as `{id, description, sample_questions, recorded}` (`recorded` is the sample questions the `replay`
provider can answer with no key), and `recorded_with`, the provider and model those recordings came from, or `null`.

### `GET /api/v1/about`

`engine.about()`: the engine version and its pipeline steps. Needs a caller.
