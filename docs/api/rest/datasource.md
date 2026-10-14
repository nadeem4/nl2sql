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
- Validates `config` as a `Datasource` and delegates to `engine.add_datasource(...)`:
  the adapter is built (not connected) and the datasource joins `engine.config`.
  Nothing is written to the config files and nothing is indexed.

Errors:
- No role -> `HTTP 401`; a role not in `policies.admin_roles` -> `HTTP 403`.
- A failure to add -> `HTTP 500` with a generic message. The exception, which can
  hold the connection string, is logged server-side and never returned.

### `GET /api/v1/datasource`

Response model: `Dict[str, Any]` (with `datasources` list)

Execution flow:
- Delegates to `engine.list_datasources()`.

### `GET /api/v1/datasource/{datasource_id}`

Response model: `Dict[str, Any]`

Execution flow:
- Validates datasource ID exists.
- Returns placeholder payload `{datasource_id, exists: true}`.

Errors:
- `ValueError` -> `HTTP 404`.
- Other exceptions -> `HTTP 500`.

### `DELETE /api/v1/datasource/{datasource_id}`

Response model: `Dict[str, Any]`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Returns a fixed payload indicating removal is not supported.

Errors:
- `ValueError` -> `HTTP 404`.
- Other exceptions -> `HTTP 500`.
