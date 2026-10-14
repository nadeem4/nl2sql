# LLM API

Source: `packages/api/src/nl2sql_api/routes/llm.py`

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

### `GET /api/v1/llm`

Response: `{"llms": {...}}`, `engine.llm.list()`: each agent by its name in
the `llm` section (`default` first) with its `provider`, `model`,
`temperature` and `inherited` (True when it sets nothing of its own). Never a
key. No role needed.

### `GET /api/v1/llm/{agent}`

Response: one agent's entry, `engine.llm.get(agent)`.

Errors:
- An unknown agent -> `HTTP 404`, naming the agents.
