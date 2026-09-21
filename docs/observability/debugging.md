# Debugging a Run with Traces

A **run trace** is one JSON file that records everything a single question did
inside the engine: every node execution in order (retries and parallel
sub-queries included), the state each node read and the update it returned, its
errors and warnings, and for every LLM call the exact messages sent, the raw
response, the parsed result and the token usage. The final `QueryResult` is in
it too.

The point is to run a failing question **once**, then debug it from the file:
read it, print its timeline, or replay it through the pipeline without calling
the model again.

## When a trace is written

| Setting | Default | Meaning |
| --- | --- | --- |
| `TRACE_MODE` | `on_failure` | `off`, `on_failure` or `always`. |
| `TRACE_DIR` | `traces` | Directory, relative to the working directory. Add it to `.gitignore` (this repository does). |
| `TRACE_SAMPLE_ROWS` | `50` | Result rows kept in the trace. |
| `TRACE_MAX_FIELD_CHARS` | `20000` | Longest string kept in a node's recorded inputs and outputs. |

`on_failure` writes a trace when the run returned errors, needed a retry in any
sub-query, had a node fail, or did not complete (timeout, cancellation, crash).
A run that recovered on retry therefore still leaves a trace.

`nl2sql demo` writes `TRACE_MODE=always` into the demo's generated `.env.demo`,
so every playground question is traced. A demo project generated before this
setting existed does not have the line; add `TRACE_MODE=always` to its
`.env.demo`, or regenerate it.

The trace is recorded by a callback that `run_with_graph` attaches to every
run, so the CLI, the Python API, the REST API and the playground all produce it.
Writing a trace never fails a run; a write error is logged as a warning.

Where the file went is reported back:

- `QueryResult.trace_path` (Python API) and `trace_path` in the REST `/query`
  response: the path, or `null` when no trace was written. `trace_id` is in the
  file name.
- `nl2sql run` prints `Trace written to <path>`.
- The playground shows a **Download trace** link under Cost & time when Debug is on.

File names are `<UTC timestamp>_<trace_id>.json`, so a directory listing sorts
by time.

## What is in the file

```json
{
  "trace_format_version": 1,
  "trace_id": "159afe1f-3305-4907-903a-55495d040665",
  "started_at": "2026-09-21T16:48:37.324431+00:00",
  "finished_at": "2026-09-21T16:48:37.712202+00:00",
  "duration_s": 0.3878,
  "outcome": "completed",
  "failed": true,
  "error": null,
  "request": {"question": "How many customers are there?", "roles": ["admin"],
              "tenant_id": "default_tenant", "datasource_id": null, "execute": true},
  "engine": {"version": "0.1.2", "git_sha": "2cfe121", "python": "3.13.14"},
  "llm": {"configured": {"default": {"provider": "openai", "model": "gpt-4o", "temperature": 0.0}},
          "by_node": {"decomposer": "gpt-4o", "ast_planner": "gpt-4o", "refiner": "gpt-4o",
                      "answer_synthesizer": "gpt-4o"}},
  "settings": {"...": "every setting except those that can hold a credential"},
  "limits": {"sample_rows": 50, "max_field_chars": 20000, "max_list_items": 200, "note": "..."},
  "nodes": ["... one entry per node execution, below ..."],
  "result": {"...": "the QueryResult, rows capped"}
}
```

One entry of `nodes` (the planner's second attempt, from a run whose first plan
named a column that does not exist; long values shortened here):

```json
{
  "seq": 20, "end_seq": 21,
  "node": "ast_planner", "sub_query_id": "sq_9579d7d41aee", "attempt": 2, "parent": "sql_agent",
  "status": "ok",
  "started_at": "2026-09-21T16:48:37.683402+00:00", "duration_s": 0.0071,
  "inputs": {"sub_query": "...", "relevant_tables": "...", "errors": "...", "retry_count": 1},
  "outputs": {"ast_planner_response": "...", "reasoning": "...", "errors": []},
  "errors": [], "warnings": [], "exception": null,
  "llm_calls": [{
    "key": {"node": "ast_planner", "sub_query_id": "sq_9579d7d41aee", "attempt": 2, "call_index": 1},
    "model": "gpt-4o",
    "params": {"model": "gpt-4o", "temperature": 0.0, "seed": 42},
    "messages": [{"role": "user", "content": "[ROLE]\nYou are a SQL Planner. ..."}],
    "prompt_sha256": "63ccb543...",
    "response": {"content": "{\"query_type\": \"READ\", ... \"column_name\": \"CustomerId\" ...}",
                 "tool_calls": [], "finish_reason": "stop", "model_name": "gpt-4o"},
    "parsed": {"query_type": "READ", "...": "..."},
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "...": "..."}
  }]
}
```

- **`seq` / `attempt` / `sub_query_id`**: `seq` orders executions by start. The
  attempt counts per node and sub-query, so a retried planner is `attempt: 2`.
  Nodes inside the SQL agent carry `parent: "sql_agent"`.
- **`status`**: `ok`, `warning` (the node returned warnings, such as
  `COLUMN_NOT_FOUND` when strict columns are off), `error` (it returned an ERROR
  or CRITICAL error, raised, or an LLM call failed) or `unfinished` (it never
  ended: the run timed out or was cancelled first).
- **`inputs`** are the state fields that node reads, not the whole graph state;
  **`outputs`** is the update it returned.
- **`llm_calls`**: the messages exactly as sent, the model's raw answer (text
  content, or tool calls with their arguments), what the node parsed it into,
  and the usage record from `TokenUsageCallback` (the same record
  `QueryResult.usage.calls` reports).

The file records what happened, including known reporting defects: a sub-query
that recovers on retry can still carry `status: "error"`, and one that exhausts
its retries on warnings can report `"success"`.

### Redaction

Every trace is redacted before it is written, with no setting to turn it off:

- every value any secret reference resolved to (`${env:...}` and the other
  secret providers), the configured API keys, and the values of environment
  variables whose names contain `KEY`, `TOKEN`, `SECRET`, `PASSWORD`,
  `CREDENTIAL`, `CONNECTION_STRING` or `AUTH`;
- anything shaped like a secret even if the engine never saw the value:
  `sk-...` style keys, `Bearer ...` tokens, the password in
  `scheme://user:password@host`, and `password=`, `pwd=`, `secret=`,
  `api_key=`, `AccountKey=`, `token=` pairs;
- the string value of any key named like `api_key`, `password`, `secret`,
  `authorization`, `access_key`, `connection_string` or `credential(s)`;
- the settings snapshot leaves out every setting whose name could hold a
  credential (`openai_api_key`, `result_artifact_adls_connection_string`).

A test runs a real question with the fake key `sk-test-not-a-real-key-1234` in
the environment and asserts that string appears nowhere in the file. Redaction
is pattern and value based, so treat a trace as internal: it contains your
prompts, schema and a sample of result rows.

### Capping

Node inputs and outputs, and the result, are capped so a trace stays readable:
result rows (`rows`, and the aggregator's `terminal_results`) keep
`TRACE_SAMPLE_ROWS` rows, other lists keep 200 items, and strings keep
`TRACE_MAX_FIELD_CHARS` characters. Every cut leaves a `[truncated]` marker
saying how much was dropped. **LLM messages and raw responses are never cut**,
because replay needs them whole; a planner prompt on Chinook is about 30 000
characters, and a whole trace is typically a few hundred kilobytes.

## Reading a trace: `nl2sql trace show`

```bash
nl2sql trace show traces/20260921T164837755710Z_159afe1f-....json
nl2sql trace show 159afe1f-3305-4907-903a-55495d040665   # looks it up in TRACE_DIR
```

It prints the question, then one row per node execution (nested SQL agent
nodes indented) with sub-query, attempt, duration, tokens and status, then
each error or warning, and marks the first failing node with `>`. A run with no
failing node but a retry marks the first warning instead, since that is what
set the retry off.

## Replaying a trace: `nl2sql trace replay`

```bash
nl2sql --env demo trace replay traces/20260921T164837755710Z_159afe1f-....json
```

Replay runs the real pipeline again for the recorded question, roles and
datasource, but every LLM call is answered from the trace. The engine's chat
clients are pointed at an in-process transport, so **no request reaches a model
provider** and no key is needed; the command reports how many recorded calls it
served. It then compares the replayed `QueryResult` with the recorded one
(ignoring ids, timings, usage and artifact paths) and says whether it matches.

- **Keying.** Recorded answers are looked up by `(node, sub_query_id, attempt,
  call_index)`, never by question text: a retried planner asks the same
  question twice and gets the answer it got the first time *for that attempt*,
  and parallel sub-queries cannot take each other's answers.
- **Divergence.** If the replayed run asks for a call the recording does not
  have, or sends a prompt whose hash differs from the recorded one, replay
  stops at that call and reports `Replay diverged at <node>, sub-query <id>,
  attempt <n>, LLM call <k>`, with a diff of the prompt when it changed. If the
  run finishes without using a recorded call, that is reported too. This is
  information, not a crash: everything before that point ran as recorded.
- **Exit code**: 0 when the replayed result matches the recording, 1 on a
  divergence, a different result, or a datasource that cannot be reached.

### What replay can and cannot tell you

- Replay tests the code **downstream of the model**: parsing, validation, SQL
  generation, execution, aggregation. It is how you check a fix to that code
  against a real failure without spending tokens.
- After a **prompt change** the recorded response is stale. Replay reports the
  node where the prompt diverged; one real run is needed to record a new trace.
- The datasource must be reachable from where you replay, with the same
  configuration (run it from the same project and `--env`). Replay says so and
  stops if it is not. Queries run again, so a database whose data changed
  gives a different result, and replay reports the difference.
- The datasource resolver and schema retriever still run their vector search,
  which embeds the question with the configured embedding provider (the local
  model in the demo; with `EMBEDDING_PROVIDER=openai` that is an embeddings API
  call, not a chat model call).
- Redaction applies to the recorded answers too: a model answer that
  contained something secret-shaped is replayed redacted.

## The playground

With **Debug** on, each node name in the per-node table under **Cost & time**
opens that node's record from the trace: every attempt and sub-query, its
errors and warnings, what it read and returned, and for LLM nodes the exact
prompt, the raw response and the parsed result. Long text is folded. The page
fetches the file from `GET /api/trace/{trace_id}`, which serves only files
directly inside `TRACE_DIR` and rejects any id that is not a plain token, so
`../` or an absolute path never reaches the filesystem.
