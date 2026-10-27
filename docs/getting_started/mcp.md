# MCP server

`nl2sql mcp` serves the engine to an MCP client, such as a desktop assistant or
an IDE, over stdio. The client gets three tools: `ask`, `list_datasources` and
`describe_table`. It is built on the official `mcp` Python SDK and uses only
the engine's public API, so it answers exactly as `engine.run_query` does.

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
| `--user ID` | The user id runs are recorded under (traces, the audit log, approvals). Optional. |

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
| `ask` | `question`, `datasource_id` (optional: skip routing), `choice` (optional: the pick for a question that asked, see below) | See below. |
| `list_datasources` | none | `{"datasources": [...]}`: each one's `id`, `type`, `description` and `status`, for the datasources the caller's roles reach. |
| `describe_table` | `datasource_id`, `table` (its name, or `schema.name`) | `{"status": "ok", "table": {...}}`: its `columns` (`name`, `type`, `nullable`, `primary_key`, `description`), `foreign_keys`, `row_count` and `description`, as [`engine.datasources.schema`](../api/core/datasource.md) gives them. |

`ask` returns:

| Key | Meaning |
| --- | --- |
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
| `clarification` | When `status` is `needs_clarification`: the `phrase`, each option's `id`, `label` and plain-English `explanation`, and the `request` itself (the plans stay in it). |

A question that can be read more than one way is not run. The assistant shows the
options to its user and calls `ask` again with the same question and
`choice={"request": <the clarification's request>, "option_id": "b"}`; the engine runs that
option's plan through the same checks as any plan, under the same role. A request that is not
this question's, or that the data or glossary has outdated, comes back as an error
(`CLARIFICATION_INVALID`, `CLARIFICATION_STALE`): ask again without a choice.

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

MCP over HTTP, with authentication, is planned for Wave C.
