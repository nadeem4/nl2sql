# Datasource API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/datasource.py`

### `DatasourceRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Dict[str, Any]` | yes | Datasource config payload. |

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

Execution flow:
- Delegates to `engine.add_datasource(...)`.

Errors:
- Unhandled exceptions return `HTTP 500`.

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

Execution flow:
- Returns a fixed payload indicating removal is not supported.

Errors:
- `ValueError` -> `HTTP 404`.
- Other exceptions -> `HTTP 500`.
