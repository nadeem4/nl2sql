# MCP server

`nl2sql mcp` serves the engine to an MCP client, such as a desktop assistant or
an IDE, over stdio, or with `--http` over HTTP behind [API keys](#over-http).
The client gets three tools: `ask`, `list_datasources` and
`describe_table`. It is built on the official `mcp` Python SDK and uses only
the engine's public API, so it answers as `engine.run_query` does.

```bash
pip install "nl2sql-engine[mcp]"
nl2sql --config my-project/nl2sql.yaml mcp --role analyst
```

The server reads and writes the MCP protocol on stdin and stdout until the
client closes the stream. Everything else, logs included, goes to stderr.

## Who is asking

stdio has no authentication: the person who started the process is the caller,
and every tool call runs as the role they name.

| Flag | Meaning |
| --- | --- |
| `--role ROLE` | A role from `policies.yaml` (repeatable). The caller reads what any of its roles allows. |
| `--user ID` | The user id runs are recorded under (traces, the audit log, approvals) and conversations belong to. Optional: without it the user id is `mcp`. |

Without `--role`, the caller has `policies.default_role`. With neither, the
server refuses to start, because a caller with no role reads nothing:

```text
Error: nl2sql mcp needs a role: pass --role <role> (repeatable), or set policies.default_role in nl2sql.yaml. A caller with no role reads nothing.
```

A role the policies do not define is refused the same way, naming the roles
there are, and a `policies.default_role` that names no role stops the config
from loading. With `policies: {allow_all: true}` (what `nl2sql init` writes)
every caller reads everything, so no role is needed: the server starts and logs
a warning that the client can read every table of every datasource. Under
`allow_all` no role is defined, so any `--role` is refused as unknown.

## Connect a client

A client that launches stdio servers takes the command and its arguments. For
example, in a client's JSON config:

```json
{
  "mcpServers": {
    "nl2sql": {
      "command": "nl2sql",
      "args": ["--config", "/path/to/my-project/nl2sql.yaml", "mcp", "--role", "analyst"]
    }
  }
}
```

The model's API key is read as for any command: from the environment, or the
`.env` next to `nl2sql.yaml`.

## The tools

Every tool returns a structured result (JSON). A failure is a `status` and a
code, never an exception's text: the details are in the server's log and the
run's trace.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `ask` | `question` (it must stand alone; optional only when sending a `choice`, which answers the pending question), `conversation_id` (optional: continue that conversation), `datasource_id` (optional: skip routing; only when starting one), `choice` (optional: the option id for a question that asked, see below) | See below. |
| `list_datasources` | none | `{"datasources": [...]}`: each one's `id`, `type`, `description` and `status`, for the datasources the caller's roles reach. |
| `describe_table` | `datasource_id`, `table` (its name, or `schema.name`) | `{"status": "ok", "table": {...}}`: its `columns` (`name`, `type`, `nullable`, `primary_key`, `description`), `foreign_keys`, `row_count` and `description`, as [`engine.datasources.schema`](../api/core/datasource.md) gives them. |

`ask` returns:

| Key | Meaning |
| --- | --- |
| `conversation_id`, `turn`, `question` | The conversation this ask belongs to (a new one without a `conversation_id`), the turn's number in it, and the question that ran. |
| `status` | The run's status: `success`, `plan_only`, `error`, `needs_approval`, `stopped`, `needs_clarification`. |
| `answer` | `{"summary", "content"}`, or `null` when no answer was written. |
| `sub_queries` | Per sub-query: `id`, `datasource_id`, `sql`, `columns`, the first 20 `rows`, `total_rows` and `truncated`. |
| `truncated` | Whether any sub-query's rows were cut, at the preview or at the row ceiling. |
| `source` | Whether a verified query or a generated one answered. |
| `warnings` | Each one's `code` and `message`, under the same rule as `errors`. |
| `trace_id` | The run's trace. The full results are read by it with `engine.results`. |
| `errors` | When the run failed: each one's `code`, `node` and `message`. A refusal (`SECURITY_VIOLATION`) always carries the generic "You do not have permission to see the data this question requires.", whatever `refusal_names_tables` says. A hook's denial, a limit, a provider failure or an answer check keeps the engine's message; any other message names only its code and the trace, since it may carry an exception's text. |
| `approvals_needed` | When `status` is `needs_approval`: what a person must approve, as `run_query` returns it ([Approval](../architecture/wave_a.md#7-approval)). |
| `stop` | When `status` is `stopped`: which limit stopped the run. |
| `clarification` | When `status` is `needs_clarification`: the `phrase`, and each option's `id`, `label` and plain-English `explanation`. The request itself stays in the conversation. |

Each `ask` is a turn of a conversation owned by the caller: without `conversation_id` one starts, with
it the question continues that conversation. The client's own model leads, so `ask` runs only the
question, with no model call of its own; write each question so it stands alone. A conversation that
is another user's, or does not exist, is `CONVERSATION_NOT_FOUND`; one running another turn is
`CONVERSATION_BUSY`; one that has reached `conversations.max_turns` is `CONVERSATION_TOO_LONG`: start a new one.
A `datasource_id` on a follow-up (it is set when the conversation starts), or no `question` and no `choice`, is
`INVALID_REQUEST`.

A question that can be read more than one way is not run. The assistant shows the
options to its user and calls `ask` again with the same `conversation_id` and
`choice="b"`, the id of the option picked; the engine runs that option's plan through the same
checks as any plan, under the same role. A choice with no `conversation_id`, an id that is not an
option of the pending question, or nothing pending is `CLARIFICATION_INVALID`; a request that the
data or glossary has outdated is `CLARIFICATION_STALE`: ask again without a choice.

An exception out of the engine is `{"status": "error", "errors": [{"code":
"INTERNAL_ERROR", ...}]}`, and logged.

## What a role cannot see

The caller sees only what its roles may read:

- `list_datasources` leaves out a datasource none of its roles reaches.
- `describe_table` answers a table its roles deny exactly as a missing one:
  `{"status": "not_found", "message": "No table 'Customer' in datasource
  'chinook'.", "tables": [...]}`, where `tables` lists the tables it may read.
  A datasource it cannot reach answers `not_found` with no tables.
- `ask` with a `datasource_id` the caller cannot reach answers
  `DATASOURCE_NOT_FOUND`, as for one that does not exist, without running.
  Without one, the run is refused by the engine's policy checks as any run is.
- `ask` never names a denied table. The client is a third party, and a
  refusal that named the table would confirm it exists, so `refusal_names_tables`
  does not apply here: every refusal carries the generic message.

Two things can still give a table away, and are the operator's to manage:

- **The refusal's code.** The message is generic, but the code is not. A plan
  that names a table the role may not read can end in `SECURITY_VIOLATION`,
  while a plan that names a table that does not exist fails a different check.
  The review of this server saw it with a role written as a deny list (the
  demo's `analyst`): a client that guesses table names can tell a denied table
  from a missing one by the code. This is how the engine's validator reports,
  not something the server adds.
- **The datasource's description.** `list_datasources` returns each
  datasource's `description` from the config, verbatim, to every caller that
  reaches it. Do not name tables there that some role may not read.

## In Python

`nl2sql.mcp.server.build_server(engine, identity)` returns the server (a
`FastMCP`); `nl2sql mcp` builds the engine and calls
`build_server(engine, identity).run("stdio")`. `identity` is a `UserContext`,
or a function returning one that is called on every tool call, inside the
tool's guard: a function that raises gives `INTERNAL_ERROR`, never its
exception's text. The engine stays yours to close.

The SDK's `FastMCP` settings read `FASTMCP_*` environment variables and the
working directory's `.env`; every setting is passed explicitly when the server
is built, so neither can change it.

```python
from nl2sql import NL2SQL, UserContext
from nl2sql.mcp.server import build_server

with NL2SQL.from_config("nl2sql.yaml") as engine:
    build_server(engine, UserContext(roles=["analyst"])).run("stdio")
```

For HTTP, `build_server(engine, identity, over_http=True)` sets the server up for
`streamable_http_app()`, and `nl2sql.mcp.http.build_app(engine, identity)` wraps it in the key
check; `nl2sql.mcp.http.serve(engine, identity)` runs it with Uvicorn. `identity` is only used
with `mcp.auth: none`.

## Over HTTP

```bash
nl2sql keys create --as admin --user ana --role analyst --label laptop   # prints the token once
nl2sql --config my-project/nl2sql.yaml mcp --http                        # http://127.0.0.1:8765/mcp
```

`nl2sql mcp --http [--host H] [--port P]` serves the same three tools at `/mcp` over the MCP SDK's
streamable HTTP transport, stateless with JSON responses: each request stands alone, and a
follow-up names its `conversation_id`. stdio stays the default. Without `--host` and `--port` the
server uses `mcp.http` in `mcp.yaml` (see [the `mcp` section](../configuration/system.md)):

```yaml
# nl2sql.yaml
mcp: mcp.yaml

# mcp.yaml (every value shown is the default)
http:
  host: 127.0.0.1
  port: 8765
  path: /mcp
  allowed_hosts: ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]   # Host headers accepted
  allowed_origins: []                              # browser Origins accepted
auth: api_keys            # api_keys | none (none: loopback only)
```

### Keys

With `auth: api_keys` every request carries `Authorization: Bearer <token>`, a token from
[`nl2sql keys create`](../security/api-keys.md). The request runs as that key's user, tenant and roles;
nothing is taken from the client, and `--role` and `--user` do not apply. Two keys in flight at once
each get their own identity. A missing, malformed, unknown or revoked key is a `401` with
`WWW-Authenticate: Bearer`, and an `mcp.denied` event the audit observer keeps (the reason and a
revoked key's id; never the token). Revoke a key and its next request fails.

`auth: none` runs every request as the one identity `--role` and `--user` name, as stdio does. It is
for trying things out on your own machine: the server refuses to bind any address but loopback with it,
and refuses to start when `allowed_hosts` lists a name that is not loopback (see below).

### Host, Origin and TLS

The SDK's DNS-rebinding protection refuses a request whose `Host` is not in `allowed_hosts`
(entries are `host` or `host:*`) or whose `Origin` is present and not in `allowed_origins`. The
default allows the loopback names only, so a server reached under another name needs that name:

```yaml
http:
  allowed_hosts: ["mcp.example.com"]
```

The server speaks plain HTTP and the key is a bearer token, so anything that is not on your own machine
needs TLS from a reverse proxy or a tunnel, and a remote chat app requires `https`. Keep the server on
`127.0.0.1` and let the proxy face the network:

```text
# Caddy: terminates TLS, forwards to the server
mcp.example.com {
    reverse_proxy 127.0.0.1:8765
}

# or a tunnel
cloudflared tunnel --url http://127.0.0.1:8765
```

The proxy or tunnel passes the public name as `Host`, so list it in `allowed_hosts`. Binding a public
address directly (`--host 0.0.0.0`) is allowed with `auth: api_keys` only. For the same reason a proxy or
tunnel needs `auth: api_keys` too: with `auth: none` the server refuses to start when `allowed_hosts` lists
any name that is not loopback, since anyone who could reach that name would read as the one identity.

### Connect a client

A client that lets its user set a header works with keys: a desktop assistant's or IDE's config file, or
a script using a model provider's remote MCP tool. For example, in a client's JSON config:

```json
{
  "mcpServers": {
    "nl2sql": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer nl2sql_<id>_<secret>" }
    }
  }
}
```

Keep the token in the client's secret store, not in a file you share. A chat app whose connector screen
offers only OAuth does not work with keys; OAuth is the next step after 0.9. The hosted demo is not
exposed over MCP.

Logs go to stderr. A request that fails to check a key (the state store is down) is a `500` and a log
line, never the token.
