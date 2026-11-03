# Settings and API keys

Source: `packages/api/src/nl2sql_api/routes/settings.py`

## Endpoints

### `PUT /api/v1/settings/llm`

Replaces the engine's whole `llm` section in the running process, through
`engine.llm.replace` (see [LLM API → llm.replace](../core/llm.md#llmreplace)).

Admin only: see [Admin routes](index.md#admin-routes).

Request body: the `llm` section as JSON, the same shape as `llm.yaml` (see
[LLM configuration](../../configuration/llm.md)).

```json
{
  "provider": "openai",
  "model": "gpt-5.4",
  "api_key": "${env:OPENAI_API_KEY}",
  "agents": {"ast_planner": {"model": "gpt-5.5"}}
}
```

Execution flow:
- The body is checked exactly as `llm.yaml` is: strict keys with "did you
  mean", the section's validators, and the providers resolved through the
  engine's registry. A `${provider:key}` reference is kept in a secret field
  (`api_key`) and resolved by the engine's secret manager, so the body can name
  a variable or a secret rather than carry a key. A reference anywhere else is
  an error: the server's environment is never read into the config.
- Every agent's API key must resolve. No model is called.
- The new clients are built, then swapped in with `engine.config.llm` as one
  step. Questions already running finish on the old LLM.
- The change is logged (one INFO line on `nl2sql.llm`) and audited, naming the
  caller's roles, `via: "api"`, and each agent's old and new provider and
  model. Nothing is written to disk: a restart reads `llm.yaml` again.

Response (`200`): the change, never a key.

```json
{
  "by": {"user_id": null, "roles": ["admin"], "via": "api"},
  "changes": {
    "default": {"old": "openai/gpt-5.4", "new": "anthropic/claude-sonnet-5-5"},
    "ast_planner": {"old": "openai/gpt-5.4", "new": "anthropic/claude-opus-5-5"}
  }
}
```

Errors:
- No role -> `HTTP 401`; a role not in `policies.admin_roles`, or a remote
  client with no role source configured -> `HTTP 403`.
- A section that is not valid, a provider that is not installed, or a key that
  does not resolve -> `HTTP 422`, whose `detail` is the config error's text.
  Nothing changes.

```text
HTTP 422
{"detail": "LLM has 1 problem:\n\n  LLM → llm.agents.ast_planer\n    Unknown key. Did you mean 'ast_planner'?"}
```

### `PATCH /api/v1/settings/llm`

Changes the provider and model of the named agents and nothing else, through `engine.llm.set_models` (see
[LLM API → llm.set_models](../core/llm.md#llmset_models)). Admin only. In memory and per process, like `PUT`: with
more than one worker, a save reaches only the one that answered, and a restart reads `llm.yaml` again.

Request body: an agent's name, as `GET /llm` lists it (`default` included), mapped to `{"provider", "model"}`, or to
`null` to drop that agent's own model so it runs on `default`.

```json
{"default": {"provider": "openai", "model": "gpt-5.4"}, "ast_planner": {"provider": "openai", "model": "gpt-5.5"}, "refiner": null}
```

`base_url`, `headers`, `prices`, temperatures and every other setting stay. Where an agent's provider changes, its
`api_key` and `base_url` are cleared, so it uses the new provider's standard key variable. Response (`200`): the same
change as `PUT`, never a key.

Errors: `401` and `403` as for `PUT`. A provider or model the server cannot use, a key that does not resolve, an
unknown agent, or `default: null` -> `HTTP 422` with the fixed message "That provider and model cannot be used on
this server."; nothing changes.

### `GET /api/v1/llm`

Any caller. Response: `{"llms": {...}}`, `engine.llm.list()`: each agent by its name in the `llm` section (`default`
first) with its `provider`, `model`, `temperature` and `inherited` (True when it sets nothing of its own). Never a key.
There is no `GET /llm/{agent}`: the list holds every agent.

### `GET /api/v1/llm/providers`

Any caller. `engine.llm.providers()`: each provider the engine can name, with its `name`, `key_variable`,
`key_present`, the verified `models` and the `key_shape` (`prefix`, `min_length`) a key must match, so a form can
check one before sending it. Never a key.

### `GET /api/v1/api-keys`

Admin only. Every key, newest first, revoked ones included: `id`, `label`, `user_id`, `tenant_id`, `roles`,
`created_by`, `created_at`, and `revoked_at`/`revoked_by` once revoked. Never a token or its hash.

### `POST /api/v1/api-keys`

Admin only. Body `{"user_id": "ann", "roles": ["analyst"], "tenant_id": null, "label": "ci"}`. Answers `201`
`{"key": {...}, "token": "nl2sql_<id>_<secret>"}`. The `token` is in this response only: the server stores a hash.
Send it as `Authorization: Bearer <token>`. An empty user, or a role that is not one of `policies.roles`, is a `422`
`INVALID_REQUEST`.

### `DELETE /api/v1/api-keys/{key_id}`

Admin only. Revokes the key; it stops verifying at once. Answers `204`. An unknown id is a `404`.

### `GET /api/v1/events/catalog`

Any caller. Every event the engine publishes (`engine.events.catalog()`): `name`, `description`, whether a run has it
(`run`), whether a caller's stream carries it, whether the audit observer writes it, and its data's JSON Schema
(`data_schema`).
