# Errors

Every non-2xx response from `nl2sql-api` is one envelope (`packages/api/src/nl2sql_api/errors.py`):

```json
{"error": {"code": "NOT_FOUND", "message": "No datasource 'sales'.", "fix": "List the datasources with GET /api/v1/datasource.",
           "details": [], "request_id": null}}
```

| Field | Meaning |
| --- | --- |
| `code` | A stable name: the facade exception's own `code` (`APPROVAL_NOT_FOUND`, `DASHBOARD_BUSY`, ...), or one of the API's own: `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `INVALID_REQUEST`, `INTERNAL`. |
| `message` | One sentence a person can act on. |
| `fix` | What to do about it, when there is something to do. |
| `details` | `422` only: one `{"loc": [...], "msg": "..."}` per problem. A rejected value is never echoed, because it may be a password. |
| `request_id` | `500` only: the id the server logged the cause under. |

Every response, error or not, carries the id in the `X-Request-ID` header.

## What becomes which status

- An `NL2SQLError` (the base of every named facade exception) is answered with its own `status` and `code`:
  for example `NotAuthorized` 403, `ApprovalNotFound` 404, `DashboardBusy` 409, `InvalidArgument` 422.
- A route or dependency that refuses on its own raises `RequestRefused(status, code, message, fix=..., details=...)`,
  with a message it wrote.
- A body or parameter that does not validate is `422` `INVALID_REQUEST` with `details`.
- An unknown route is `404` `NOT_FOUND`; a wrong method is `405`.
- Anything else is `500` `INTERNAL`: `"Something failed on the server. Request id: <id>"`. A built-in
  `ValueError`, `KeyError`, `LookupError` or `PermissionError`, or a pydantic `ValidationError`, is never mapped to a
  4xx, because its text may hold a connection string. The exception is logged with the id.

In an OpenAPI route, declare the envelope with `responses={404: ERROR_RESPONSE}` (from `nl2sql_api.errors`), or use
`identity.AUTH_RESPONSES` and `identity.ADMIN_RESPONSES`.

`POST /api/v1/query` is the exception for a pipeline failure: it is `200` with the errors in `QueryResponse.errors`.
