# Access and Security

Who is asking, what they may read, and who owns what they create, for contributors and operators who need to reason about access.

This page is the map. The properties and limits, including what the engine does not protect, are in the [Security model](../security/model.md); read it before exposing a server. It is not repeated here.

## One caller type

Every call carries a `UserContext`: `user_id`, `tenant_id` and `roles` (`packages/sdk/src/nl2sql_sdk/policy.py`). The engine believes the context it is given. Authentication is the job of whatever sits in front of it.

The REST API resolves one `UserContext` per request from the source `api.auth` names (`packages/api/src/nl2sql_api/identity.py`):

| `api.auth` | Where the caller comes from |
| --- | --- |
| `api_keys` | `Authorization: Bearer <token>`, verified by `engine.api_keys` ([API keys](../security/api-keys.md)) |
| `proxy` | user, tenant and roles headers from a proxy at an address listed in `api.proxy.trusted_from` |
| `static` | one role (`api.static_role`) for every request |
| `none` | the local operator, for a loopback request only |

With `none`, the server refuses to bind a non-loopback host, and answers only requests whose connecting peer and `Host` are loopback and that carry no forwarding header. A route never reads the caller from a request body.

## One access rule

A datasource's stored connection sets the most anyone can see through it. A person's roles and policies narrow that, never widen it. Every path to data uses the same checks:

- A **scope** (`nl2sql/policies/scope.py`) says what one caller may read in one datasource. The datasource resolver, the curated matcher, the schema retriever, the logical validator and the executor's value check all ask it, and a run builds each scope once so every step decides alike.
- Policy is **strict and fail-closed**. A plan that touches a table the role may not read is refused before any SQL exists, never answered from the tables the role can see. An unknown role grants nothing. An optional policy check is intersected with the roles, and an exception from it denies the datasource.
- A question, a dashboard `open`, a refresh, a schedule and a saved answer's run all go through `run_query` with a `UserContext`, so none has a side door.
- An **admin** is a caller holding a role listed in `policies.admin_roles`. Admin routes check that and nothing else.

## Owners

What a person creates is theirs, keyed by `(tenant_id, user_id)`:

- conversations, dashboards, workspaces and saved answers: another caller sees "not found", the same error as a missing id;
- a run's results and trace: read only by the caller who asked (`engine.results.*` and `engine.traces.get` take a required `user_context`), and a trace shown to anyone else redacts the rows and the operator's configuration;
- a background job's result: the owner's alone; an admin sees status, kind, progress and error only.

Every app passes the caller on each such read; `test_boundaries.py` fails if one omits it.

## Approvals

A hook can hold a run for a person's yes. The engine stores the pending request (`nl2sql/approvals/`), an approver holding a role the hook names grants or denies it by id, and the asker's re-run names the id. Deciding is limited to the hook's approver roles (`NotAuthorized` otherwise). A grant runs nothing itself. It is bound to the asker and to the SQL that was asked for, so only the asker's own re-run of that SQL can use it. Requests and grants expire after `settings.approvals.max_age_hours`.

## The hosted demo

`nl2sql-api serve --demo --hosted` is a public server where nobody is trusted (`packages/api/src/nl2sql_api/hosted.py`).

- Each browser is an anonymous visitor. A random token in an `HttpOnly` cookie is signed with an HMAC key the server keeps in `<storage.path>/hosted_session.key`; a token it did not sign is replaced, and a restart keeps every cookie valid.
- The token only finds the session. The server turns it into a one-way visitor id, and that id is the `user_id` for everything the engine stores, with one non-admin role. No header can make a visitor an admin.
- The server holds no model key and refuses to start if its environment has one. Visitors bring their own, which travels with each request and is never written to disk.
- Questions, turns and jobs spend from per-address and per-session limits; a refusal is a 429 sent before the route runs, so it starts no model call.

## Why

Putting the whole decision in one scope object means a new feature cannot forget the rule: it either asks the scope or it does not touch data. Keeping the owner in the key, instead of in a permissions table, means a new record type is private by default.
