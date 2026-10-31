# REST API (`api`)

The `api` section of [`nl2sql.yaml`](system.md) holds `nl2sql-api`'s own
settings. It is optional. The engine validates it like every other section
(strict keys, `${env:...}` references) and never reads it; `nl2sql-api` reads
`engine.config.api`.

```yaml
# nl2sql.yaml
api: { file: api.yaml }
```

```yaml
# api.yaml
auth: none                      # api_keys | proxy | static | none (default none)
proxy:                          # auth: proxy only
  trusted_from: [127.0.0.1/32, "::1/128"]
  user_header: X-Forwarded-User
  tenant_header: null           # null: settings.tenant_id
  roles_header: X-NL2SQL-Roles
static_role: null               # auth: static only
request_llm: false              # callers may send their own key and models
cors_origins: [https://app.example.com]
```

Wave D adds the fields below. The engine validates them now; `nl2sql-api` starts reading them when its identity replaces `auth.py` (D0a-2), which also deletes `role_header`, `role` and `trust_body_role`.

```yaml
auth: none                      # api_keys | proxy | static | none (default none)
proxy:                          # auth: proxy only
  trusted_from: [127.0.0.1/32, "::1/128"]
  user_header: X-Forwarded-User
  tenant_header: null           # null: settings.tenant_id
  roles_header: X-NL2SQL-Roles
static_role: null               # auth: static only
request_llm: false              # callers may send their own key and models
```

| Field | Default | Meaning |
| --- | --- | --- |
| `auth` | `none` | How a caller is identified: `api_keys`, `proxy`, `static` or `none`. |
| `proxy.trusted_from` | loopback | CIDRs the proxy connects from; its headers are honoured only from these. |
| `proxy.user_header`, `proxy.tenant_header`, `proxy.roles_header` | `X-Forwarded-User`, none, `X-NL2SQL-Roles` | The headers the proxy sets. |
| `static_role` | none | The one role of every request under `auth: static`. |
| `request_llm` | `false` | Whether callers may send their own provider key and models per request. |

| Field | Default | Meaning |
| --- | --- | --- |
| `auth` | `none` | How a caller is identified: `api_keys`, `proxy`, `static` or `none`. See [Identity](../api/rest/index.md#identity). |
| `proxy.trusted_from` | loopback | CIDRs the proxy connects from; its headers are honoured only from these. |
| `proxy.user_header`, `proxy.tenant_header`, `proxy.roles_header` | `X-Forwarded-User`, none, `X-NL2SQL-Roles` | The headers the proxy sets. |
| `static_role` | none | The one role of every request under `auth: static`. |
| `request_llm` | `false` | Whether callers may send their own provider key and models per request. |
| `cors_origins` | `[]` | Browser origins allowed to call the API. Empty, no cross-origin request is allowed. |

The 0.9 fields `role_header`, `role` and `trust_body_role` are gone, and the
engine rejects them as unknown keys. Use `auth: proxy` (the roles header is
`proxy.roles_header`), `auth: static` with `static_role`, and nothing for the
body role: a request body never carries the caller.

## Admin routes

The routes that change datasources, LLMs or the index need one of the roles in
`policies.admin_roles` (default `[admin]`), which is a policy, so it lives in
the [`policies` section](policies.md), not here. A request with another role
is a 403. Under `auth: none` the caller is the local operator, who holds the
admin roles. See [Admin routes](../api/rest/index.md#admin-routes).

## Starting `nl2sql-api`

`nl2sql-api serve` builds its engine from `--config` (else `NL2SQL_CONFIG`, else
`./nl2sql.yaml`), after loading the `.env` next to that file without overriding
a variable already set. It calls `engine.check()` before it accepts requests: a
`required` datasource that fails, or datasources configured and none ready,
stops it with `EngineNotReady`; an optional one that fails leaves it serving
the others. See [Startup](system.md#startup-check-and-required).

```bash
nl2sql-api serve --config /etc/nl2sql/nl2sql.yaml --host 127.0.0.1 --port 8000
# or: NL2SQL_CONFIG=/etc/nl2sql/nl2sql.yaml uvicorn nl2sql_api.main:app
```

`serve` runs one worker, and with `auth: none` it refuses to bind any host but
loopback, naming `api.auth` in the message.

The `NL2SQL_API_*` variables of 0.4 are not read; see
[From 0.4](system.md#from-04).
