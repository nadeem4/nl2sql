# Health API

Source: `packages/api/src/nl2sql_api/routes/health.py`. None of these routes needs a caller except `/about`.

## Endpoints

### `GET /api/v1/health`

Liveness. Returns `{"status": "ok", "version": {"engine": "<nl2sql-engine>", "api": "<nl2sql-api>"}}`.

### `GET /api/v1/ready`

Readiness. Returns `{"success": true, "data": null, "message": "NL2SQL API is ready"}`. It does not yet check
datasources, the LLM or the index.

### `GET /api/v1/meta`

What the web app needs before it has an identity: `{"mode": "demo" | "your_data" | "hosted", "auth": "api_keys" |
"proxy" | "static" | "none", "request_llm": false}`. No caller is needed.

### `GET /api/v1/about`

`engine.about()`: the engine version and its pipeline steps. Needs a caller.
