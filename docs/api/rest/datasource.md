# Datasource API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/datasource.py`

### `DatasourceRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Dict[str, Any]` | yes | One datasource, in the shape of a `datasources` entry of `nl2sql.yaml`: `id`, `type`, `connection`, and optionally `description`, `limits`, `preferred_tables`, `sample_questions`, `required`. See [Datasources](../../configuration/datasources.md). |

### `DatasourceResponse`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `success` | `bool` | yes | Operation status. |
| `message` | `str` | yes | Result message. |
| `datasource_id` | `Optional[str]` | no | Datasource ID. |

## Endpoints

### `POST /api/v1/datasource`

Source: `packages/api/src/nl2sql_api/routes/datasource.py`

Request model: `DatasourceRequest`

Response model: `DatasourceResponse`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Validates `config` as a `Datasource` and delegates to `engine.datasources.add(...)`:
  the adapter is built (not connected) and the datasource joins `engine.config`.
  Nothing is written to the config files and nothing is indexed.

Errors:
- No role -> `HTTP 401`; a role not in `policies.admin_roles` -> `HTTP 403`.
- A failure to add -> `HTTP 500` with a generic message. The exception, which can
  hold the connection string, is logged server-side and never returned.

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
