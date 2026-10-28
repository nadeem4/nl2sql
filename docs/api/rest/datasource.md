# Datasource API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/datasource.py`

### `DatasourceTestRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Dict[str, Any]` | yes | One datasource, in the shape of a `datasources` entry of `nl2sql.yaml`: `id`, `type`, `connection`, and optionally `description`, `limits`, `preferred_tables`, `sample_questions`, `required`. See [Datasources](../../configuration/datasources.md). |

A key the model does not know is an error, so a typo is found, not ignored. (`user_context` is the one extra key
the body may hold: `api.trust_body_role` reads the caller from it.)

### `DatasourceRequest`
`DatasourceTestRequest` and:

| field | type | required | meaning |
| --- | --- | --- | --- |
| `save` | `bool` | no | Also write the datasource to `datasources/<id>.yaml` (default `false`). |

### `DatasourceResponse`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `success` | `bool` | yes | Operation status. |
| `message` | `str` | yes | Result message. |
| `datasource_id` | `Optional[str]` | no | Datasource ID. |
| `file` | `Optional[str]` | no | With `save`, the file written, relative to the project (`datasources/sales.yaml`). |

## Endpoints

The three Connect routes (`GET /datasource-types`, `POST /datasource/test`, `POST /datasource`) are what a
Connect a database form calls, in that order. They are admin only and are thin over `engine.datasources`.

### `GET /api/v1/datasource-types`

Source: `packages/api/src/nl2sql_api/routes/datasource.py`

Admin only: see [Admin routes](index.md#admin-routes).

Response: `{"types": [...]}`, `engine.datasources.types()`: every adapter the engine can name, installed or not,
sorted by name, each with `name`, `title`, `installed`, `install_hint` (what to install when a driver is
missing) and `connection_schema`, the JSON Schema of its connection model. A secret field is
`"format": "password", "writeOnly": true`; a field in the `advanced` group carries `"group": "advanced"`.

### `POST /api/v1/datasource/test`

Request model: `DatasourceTestRequest`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Validates `config` as a `Datasource` and delegates to `engine.datasources.test(datasource)`: the adapter is
  built, its connection tested and closed. **Nothing is registered, written or indexed.**

Response: the status, `{"id", "status", "required", "stage", "reason", "fix", "warnings"}`. A failure is a
`200` with `status: "failed"`, the `stage` it failed at (`config`, `driver`, `network`, `auth`, `database` or
`permission`), the `reason` and the `fix`. No value is echoed: the datasource's secret literals and every secret
the engine knows are taken out of the driver's words.

Errors:
- A body that does not validate -> `HTTP 422`, as for `POST /datasource`.

### `POST /api/v1/datasource`

Request model: `DatasourceRequest`

Response model: `DatasourceResponse`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Validates `config` as a `Datasource` and delegates to `engine.datasources.add(...)`:
  the adapter is built (not connected) and the datasource joins `engine.config`.
  Nothing is indexed.
- With `"save": true`, then `engine.datasources.save(id, by=caller, env_file=...)` writes
  `datasources/<id>.yaml` beside `nl2sql.yaml` (see [Saving a datasource](../../configuration/datasources.md#saving-a-datasource)).
  **A secret is never written into the YAML.** A reference (`${env:VAR}`, `${vault:path}`) is written as it is.
  A literal is written to the `.env` beside `nl2sql.yaml` as `${env:NL2SQL_<ID>_<KEY>}` only on a fresh local
  install: no role source is configured (`api.role_header`, `api.role`, `api.trust_body_role`) and the client is
  on loopback. Anywhere else a literal secret with `save: true` is a `422` that asks for a `${env:...}` reference.
  If the save fails, the datasource is taken out again: nothing is left running that was not saved.

Errors:
- No role -> `HTTP 401`; a role not in `policies.admin_roles` -> `HTTP 403`.
- A body that does not validate, a typo included -> `HTTP 422` and `detail` is a list of
  `{"path": "config.sampel_questions", "message": "Extra inputs are not permitted"}`, one per problem, each
  naming its path in the body and never a value.
- An id that is already taken, a connection that does not validate, a literal secret that cannot be written, or
  two secrets that map to one environment variable -> `HTTP 422` with the reason as text (it names fields,
  never values).
- `save` found `datasources/<id>.yaml` changed on disk since the engine loaded it, or a new file that already
  exists -> `HTTP 409`; reload the engine and try again.
- Any other failure to add -> `HTTP 500` with a generic message. The exception, which can hold the connection
  string, is logged server-side and never returned.

### `GET /api/v1/datasource`

Response: `{"datasources": [...]}`, `engine.datasources.list()`: every
configured datasource with its `id`, `type`, `description`, `status`
(`ready`, `failed` with a `reason`, or `unchecked`), `capabilities` and
`connection` with every secret masked. No role needed.

```json
{"datasources": [
  {"id": "shop", "type": "postgres", "description": "Orders and customers", "status": "ready",
   "capabilities": ["supports_sql"], "connection": {"host": "db", "user": "app", "password": "***"},
   "reason": null}
]}
```

### `GET /api/v1/datasource/{datasource_id}`

Response: one datasource, as the list shows it (`engine.datasources.get(...)`).

Errors:
- An unknown id -> `HTTP 404`.

### `DELETE /api/v1/datasource/{datasource_id}`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Delegates to `engine.datasources.remove(...)`: the datasource leaves routing
  and `engine.config`, and its connection pool is closed. Nothing is written to
  the config files, and its index entries stay until the next full index.

Response: `{"success": true, "message": "...", "datasource_id": "..."}`.

Errors:
- An unknown id -> `HTTP 404`.
- Other exceptions -> `HTTP 500`.
