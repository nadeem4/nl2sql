# Datasources API

The request and response models of the datasource routes, for developers calling them over HTTP.

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/datasources.py`

### `DatasourceTestRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Dict[str, Any]` | yes | One datasource, in the shape of a `datasources` entry of `nl2sql.yaml`: `id`, `type`, `connection`, and optionally `description`, `limits`, `preferred_tables`, `sample_questions`, `required`. See [Datasources](../../configuration/datasources.md). |

A key the model does not know is an error, so a typo is found, not ignored. The body never carries the caller.

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

The three Connect routes (`GET /datasource-types`, `POST /datasources/test`, `POST /datasources`) are what a
Connect a database form calls, in that order. They are admin only and are thin over `engine.datasources`.

### `GET /api/v1/datasource-types`

Source: `packages/api/src/nl2sql_api/routes/datasources.py`

Admin only: see [Admin routes](index.md#admin-routes).

Response: `{"types": [...]}`, `engine.datasources.types()`: every adapter the engine can name, installed or not,
sorted by name, each with `name`, `title`, `installed`, `install_hint` (what to install when a driver is
missing) and `connection_schema`, the JSON Schema of its connection model. A secret field is
`"format": "password", "writeOnly": true`; a field in the `advanced` group carries `"group": "advanced"`.

### `POST /api/v1/datasources/test`

Request model: `DatasourceTestRequest`

Admin only: see [Admin routes](index.md#admin-routes).

Execution flow:
- Validates `config` as a `Datasource` and delegates to `engine.datasources.test(datasource)`: the adapter is
  built, its connection tested and closed. **Nothing is registered, written or indexed.**

Response: the status, `{"id", "status", "required", "stage", "reason", "fix", "warnings"}`. A failure is a
`200` with `status: "failed"`, the `stage` it failed at (`config`, `driver`, `network`, `auth`, `database` or
`permission`), the `reason` and the `fix`. No value is echoed: the datasource's secret literals (a plugin's secret
fields included) and every secret the engine knows are replaced by `***` in the driver's words, and only those
exact values: a validation message such as `connection.password: Field required` stays readable.

Errors:
- A body that does not validate -> `HTTP 422`, as for `POST /api/v1/datasources`.

### `POST /api/v1/datasources`

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
  A literal is written to the `.env` beside `nl2sql.yaml` as `${env:NL2SQL_<ID>_<KEY>}` only when
  `api.auth` is `none`, which answers only a request made straight to localhost (a `Host` that is not
  loopback, or an `X-Forwarded-For`, `Forwarded` or `X-Real-IP` header, is already a `403`). Under any other `api.auth` a literal secret with `save: true` is a `422` that asks for a `${env:...}` reference.
  If the save fails, the datasource is taken out again: nothing is left running that was not saved. The one exception
  is a read-only config (Docker's mounted config): `403 NOT_WRITABLE`, and the datasource stays registered until the
  server restarts.

Errors:
- Every error is the [error envelope](errors.md). No identity -> `HTTP 401`; a role not in `policies.admin_roles` -> `HTTP 403`.
- A body that does not validate, a typo included -> `HTTP 422` and `error.details` is a list of
  `{"loc": ["config", "sampel_questions"], "msg": "Extra inputs are not permitted"}`, one per problem, each
  naming its place in the body and never a value. That includes a body that is not a JSON object or not JSON at
  all (`[{"loc": [""], "msg": "The body must be a JSON object."}]`, no input echoed), and a `connection` that
  does not validate against the adapter's model (`config.connection.passwrd`, `config.connection.port`).
- A literal secret that cannot be written (`SecretValueInConfig`) or a connection the adapter rejects (`ConnectionInvalid`) -> `HTTP 422`
  with the engine's message and `fix`. An id that is already taken, or two secrets that map to one environment variable,
  is the engine's own named error, with its status and code.
- An unknown id on a `/datasources/{id}` route, or one the caller's scope hides -> `HTTP 404` `NOT_FOUND`.
- `save` found `datasources/<id>.yaml` changed on disk since the engine loaded it, or a new file that already
  exists -> `HTTP 409`; reload the engine and try again.
- Any other failure -> `HTTP 500` `INTERNAL` naming a request id. The exception, which can hold the connection
  string, is logged server-side under that id and never returned.


### `GET /api/v1/datasources`

Response: a list of `DatasourceInfo` from `engine.datasources.list(user_context=caller)`: the datasources the caller's
scope reaches, each with `id`, `type`, `description`, `status` (`ready`, `failed` with a `reason`, or `unchecked`; the `reason` is the driver's text for an admin and a generic sentence for anyone else),
`capabilities`, `connection` with every secret masked, and `sample_questions`. No role needed.

### `GET /api/v1/datasources/{datasource_id}`

Response: one datasource, as the list shows it. An unknown id, or one the caller cannot reach -> `HTTP 404`.

### `DELETE /api/v1/datasources/{datasource_id}`

Admin only: see [Admin routes](index.md#admin-routes).

Delegates to `engine.datasources.remove(...)`: the datasource leaves routing and `engine.config`, and its connection
pool is closed. Nothing is written to the config files, and its index entries stay until the next full index.
Response: `{"success": true, "message": "...", "datasource_id": "..."}`. An unknown id -> `HTTP 404`.

### `GET /api/v1/datasources/{datasource_id}/schema`

The tables and columns as the caller may see them: a table or column the caller's scope denies is left out.
An unknown or hidden datasource -> `HTTP 404`.

### `GET /api/v1/datasources/{datasource_id}/diagram`

The schema as a Mermaid `erDiagram`, as `text/plain`. Repeat the `tables` query parameter to draw those tables and the
ones one relationship away; leave it out for the whole datasource (cut to the 30 with the most relationships).
`tables` that match no table the caller may see -> `HTTP 422` `INVALID_ARGUMENT`.
