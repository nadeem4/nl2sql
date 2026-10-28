# API keys

An API key authenticates a client of MCP over HTTP (arriving with Wave C's M2). It carries the user,
tenant and roles its holder runs as, so a key per person or per app is the unit of revocation. Nothing
is inferred from the client. The design is [Wave C, section 9](../architecture/wave_c.md#9-mcp-over-http-with-auth).

```text
nl2sql keys create --as admin --user ana --role analyst --tenant acme --label laptop
nl2sql keys list --as admin
nl2sql keys revoke <id> --as admin
```

`create` prints the token once. Keep it in the client's secret store: only its hash is kept here, so a
lost token is replaced by a new key, not recovered.

## The token

`nl2sql_<id>_<secret>`: `id` is 8 hex characters, shown in lists, logs and events; `secret` is 32
random bytes, URL-safe (43 characters). The state store keeps `sha256(secret)` and the key's fields,
never the token. SHA-256 with no salt is deliberate: the secret is 256 bits of randomness, so there is
nothing to guess and a slow hash would only cost time on every request. The comparison uses
`hmac.compare_digest`.

## From Python

```python
from nl2sql import NL2SQL, UserContext

admin = UserContext(user_id="root", roles=["admin"])

key, token = engine.api_keys.create(user_id="ana", roles=["analyst"], by=admin, tenant_id="acme", label="laptop")
engine.api_keys.list(by=admin)            # [ApiKey, ...], newest first, revoked ones included; no secret
engine.api_keys.revoke(key.id, by=admin)  # stops verifying at once
engine.api_keys.verify(token)             # UserContext(user_id="ana", tenant_id="acme", roles=["analyst"]) or None
```

| Method | Behaviour |
|---|---|
| `create(*, user_id, roles, by, tenant_id=None, label="")` | Returns the `ApiKey` and its token, once. `roles` must be configured in `policies.roles`; `user_id` must not be empty. An id that is already taken is retried with a new one and never replaces another key. Publishes `api_key.created`. |
| `list(*, by)` | Every key, newest first. `ApiKey` has no secret and no hash. |
| `revoke(key_id, *, by)` | Sets `revoked_at`; revoking a revoked key changes nothing. An unknown id is a `ValueError`. Publishes `api_key.revoked`. |
| `verify(token)` | The key's `UserContext`, or `None` for a malformed, unknown or revoked token, or a wrong secret. It takes no caller: it is how a caller is found. |

`by` is required and has no default. A call without one is a `TypeError`, a `by` that is not a
`UserContext` is a `TypeError`, and a `by` holding none of `policies.admin_roles` is a
`PermissionError`. Under `policies.allow_all` every caller is an admin, as for the rest of the engine,
and any role name is accepted when no roles are defined.

## Events and logs

`api_key.created` and `api_key.revoked` carry `key_id`, `user_id`, `tenant_id`, `roles` and `by`, and
the audit observer keeps both. Neither the token nor any part of the secret appears in an event, a log
line or an error. See [Events](../observability/events.md).

## What a key does and does not give

A key proves that the holder has the token; it does not prove who the holder is, and it does not expire.
Revoke a key when a person leaves or a token leaks. A key's roles are checked against the policies at
every request, so removing a role from `policies.roles` stops what the key can read, though not the key.
