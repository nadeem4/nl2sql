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

Each engine reads these from the `settings.traces` section of its config, so
two engines in one process trace by their own settings. The 0.4 variable each
one is still set from by the CLI and the playground is in brackets.

| Setting | Default | Meaning |
| --- | --- | --- |
| `settings.traces.mode` | `on_failure` | `off`, `on_failure` or `always`. |
| `settings.traces.sample_rows` | `50` | Result rows kept in the trace. |
| `settings.traces.max_field_chars` | `20000` | Longest string kept in a node's recorded inputs and outputs. |

Traces are written through the trace store in `storage.traces` (see
[Storage](../configuration/storage.md)). The built-in `files` store writes them
to `<storage.path>/traces/`; `nl2sql init` puts `storage.path` in
`.gitignore`. A trace store of your own (the `TraceStore` protocol in
`nl2sql_sdk.stores`: `write`, `get`, `purge`) receives the whole document.

Traces are kept for the store's `max_age_days`, 14 days by default
(`traces: { type: files, max_age_days: 30 }`). `engine.storage.purge()` deletes
the older ones; `nl2sql storage purge` calls it, and `nl2sql-api` calls it at
startup and once a day.

`on_failure` writes a trace when the run returned errors, needed a retry in any
sub-query, had a node fail, or did not complete (timeout, cancellation, crash).
A run that recovered on retry therefore still leaves a trace.

The demo's config (`nl2sql init --demo`) sets `settings.traces.mode: always`
in its `settings.yaml`, so every playground question is traced.

The trace is recorded by a callback that `run_with_graph` attaches to every
run, so the CLI, the Python API, the REST API and the playground all produce it.
Writing a trace never fails a run; a write error is logged as a warning.

Where the file went is reported back:

- `QueryResult.trace_path` (Python API) and `trace_path` in the REST `/query`
  response: the path, or `null` when no trace was written or the trace store is
  not the `files` store. `trace_id` is in the file name, and
  `engine.traces.get(trace_id)` reads the trace from any store.
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
              "tenant_id": "default_tenant", "datasource_id": null, "execute": true, "clarify": true, "answer": true,
              "today": "2026-09-21"},
  "engine": {"version": "0.1.2", "git_sha": "2cfe121", "python": "3.13.14"},
  "llm": {"configured": {"default": {"provider": "openai", "model": "gpt-5.4", "temperature": 0.0}},
          "by_node": {"decomposer": "gpt-5.4", "ast_planner": "gpt-5.4", "refiner": "gpt-5.4",
                      "answer_synthesizer": "gpt-5.4"}},
  "settings": {"...": "the run's settings section: pipeline, retrieval, results, traces, schema, ..."},
  "limits": {"sample_rows": 50, "max_field_chars": 20000, "max_list_items": 200, "note": "..."},
  "nodes": ["... one entry per node execution, below ..."],
  "events": ["... the run's bus events, below ..."],
  "result": {"...": "the QueryResult, rows capped"}
}
```

`request` holds what a replay needs to ask the same question again: the
`question`, caller (`roles`, `tenant_id`), `datasource_id`, and the `execute`,
`clarify` and `answer` flags, plus `today`, the date the prompts were told.

`events` lists the run's own [events](events.md) in the order they were
published (`run.started`, each `node.started` and `node.finished` with its
`sub_query_id`, `result.manifest`, `llm.call` and the rest), every one but
`answer.delta`, whose text the result holds whole. Each is the event as JSON:
`name`, `at`, `trace_id`, `tenant_id` and `data`. The trace is written before
the run's `run.finished` is published, so the list ends just before it; the
trace's `result` is what that event summarises.

A log line written while the run was in flight carries the same `trace_id`,
on the caller's thread and on every graph worker thread, so grepping the
logs for a trace's id finds its lines ([Events](events.md#logs-carry-the-runs-trace_id)).

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
    "model": "gpt-5.4",
    "params": {"model": "gpt-5.4", "temperature": 0.0, "seed": 42},
    "messages": [{"role": "user", "content": "[ROLE]\nYou are a SQL Planner. ..."}],
    "prompt_sha256": "63ccb543...",
    "response": {"content": "{\"query_type\": \"READ\", ... \"column_name\": \"CustomerId\" ...}",
                 "tool_calls": [], "finish_reason": "stop", "model_name": "gpt-5.4"},
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
  `QueryResult.usage.calls` reports). `params` holds what was sent: an agent
  configured with `temperature: null` has no `temperature` there, and
  `llm.configured` shows it as `null`. `model` is the model the provider says
  answered, per call, so per-node models show up per node in `llm.by_node`.

- **Plan cache hits.** When a sub-query's plan came from the plan cache, its
  `ast_planner` entry has `outputs.ast_planner_response.plan_source: "cache"`
  and `llm_calls: []` (zero planner tokens), and the validator, generator and
  executor entries follow as usual. The result shows the same thing as
  `result.sub_queries[].plan_source` and `result.usage.plan_cache_hits`. See
  [Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

### Retrieval

The `datasource_resolver` and `schema_retriever` entries carry a `retrieval`
object in `outputs`: what the vector search retrieved and how MMR chose among it
(the engine has no re-ranking model; see
[Indexing](../architecture/indexing.md#retrieval-mmr-not-a-re-ranking-model)).

```json
"retrieval": {
  "skipped": false,
  "query": "Tracks sold per genre, highest first\ngroup_by: genre name ...",
  "searches": [{
    "search": "tables",
    "filter": {"datasource_id": "chinook", "types": ["schema.table", "schema.metric"]},
    "k": 8, "fetch_k": 32, "lambda_mult": 0.7,
    "pool": [
      {"rank": 1, "id": "schema.table:[main].[Track]:<version>", "label": "[main].[Track]",
       "type": "schema.table", "datasource_id": "chinook", "table": "[main].[Track]", "column": null,
       "similarity": 0.5651, "distance": 0.8698,
       "picked": true, "pick_order": 1, "mmr_score": 0.5651, "redundancy": 0.0}
    ],
    "picks": ["<ids in the order MMR picked them>"],
    "dropped": ["<ids of the pool entries not picked>"]
  }],
  "tables": [{"table": "Track", "columns": ["Name", "GenreId"], "qualified": "[main].[Track]", "steps": ["vector"]}]
}
```

On a schema past `settings.retrieval.full_snapshot_max_tables`, each sent table
carries `steps`: which of `vector`, `glossary`, `selector` and `join_path` added
it, so a missed table can be traced to its cause (see
[the schema retriever](../architecture/nodes/schema_retriever_node.md#table-recall-on-large-schemas)).

- **`query`** is the text actually embedded: the question for the resolver's routing, and
  for the schema retriever the sub-query's intent, filters, group-by, expected
  columns and metrics, one per line.
- **`pool`** is every candidate, nearest first. `similarity` is the cosine
  similarity MMR uses; `distance` is Chroma's (squared L2). The resolver's
  routing searches record no distance: each ranks its pool by similarity
  alone (recorded as `lambda_mult` 1), and its picks are each datasource's
  best entry. `pick_order` is the
  order MMR picked in, `mmr_score` the score the pick won with, and
  `redundancy` its similarity to the closest earlier pick.
- **`searches`** lists each search in order; the schema retriever runs `tables`
  (or `columns` when that finds nothing), then `planning` over the tables found.
  The resolver runs one search per routing signal (`route.description`,
  `route.examples`, `route.curated`, `route.tables`, `route.columns`) and ends
  with a `routing` record: each signal's datasource ranking (`signals`), the
  fused reciprocal-rank scores with each datasource's ranks (`fused`), the
  datasources kept (`picks`) and those dropped (`dropped`, with `floor` or
  `limit`), and the `rrf_k`, `vote_k`, `candidates` and `floor_ratio` used. See
  [Routing](../architecture/indexing.md#routing-five-signals-fused).
- **`tables`** (schema retriever) is what survived: the tables and columns sent
  to the planner.
- **`skipped: true`** with a `reason` means no search ran: the schema is small
  enough to send whole (each of the demo's three schemas, at 11, 4 and 5 tables,
  always is), only one datasource is registered (`"single datasource: vector
  search skipped"`, which the demo is not: it registers three, so the resolver
  searches on every question), the request named a `datasource_id`, or there is
  no vector store. The resolver's answerability check is an LLM call, so it is in
  that entry's `llm_calls`, not in `retrieval`.

The record carries entry ids and names, never an entry's embedded text, which
for a column holds its statistics and sample values. A table the run's role
may not read is never searched at all (the searches run over the readable
tables only, and the filter records how many as `searched_tables`), so it
never appears in the record. The record is built from the
numbers MMR computes anyway, so it is always there and costs no extra search.

The file records what happened. A sub-query's `status` reflects its final
attempt, so one that recovers on retry reports `"success"`; the failed
attempts stay in the node entries and in the result's `warnings`.

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
- the settings snapshot is the `settings` section only, which holds no
  credential: keys live in the `llm`, `embeddings` and `datasources` sections
  as references, and their resolved values are redacted as above.

A test runs a real question with the fake key `sk-test-not-a-real-key-1234` in
the environment and asserts that string appears nowhere in the file. Redaction
is pattern and value based, so treat a trace as internal: it contains your
prompts, schema and a sample of result rows.

### Capping

Node inputs and outputs, and the result, are capped so a trace stays readable:
result rows (`rows`, and the aggregator's `terminal_results`) keep
`settings.traces.sample_rows` rows, other lists keep 200 items, and strings keep
`settings.traces.max_field_chars` characters. Every cut leaves a `[truncated]` marker
saying how much was dropped. **LLM messages and raw responses are never cut**,
because replay needs them whole; a planner prompt on Chinook is about 12 000
characters, and a whole trace is typically a few hundred kilobytes.

## Why this answer: the explanation

Before reading a trace, read `QueryResult.explanation`: each sub-query's plan
in plain English (its tables, joins and where each join came from, filters,
groupings, measures and order), the glossary terms and rules its prompt
carried, and the assumptions the answer made (a reading not chosen, a filter
value replaced after `VALUE_NOT_FOUND`, a sanity warning, a result cut at the
row cap). It is rendered from the typed plan with no model call, so it says
what the SQL does, not what a model thinks it does; the planner's own
`reasoning` is kept apart as `planner_note`. A result rebuilt from a trace
(`nl2sql trace replay`, `engine.traces`) carries it too, since it is rendered
from the same state. See [Explanation](../api/core/query.md#explanation).

## Reading a trace: `nl2sql trace show`

```bash
nl2sql trace show traces/20260921T164837755710Z_159afe1f-....json
nl2sql trace show 159afe1f-3305-4907-903a-55495d040665   # looks it up in the trace store (engine.traces.get)
```

It prints the question, then one row per node execution (nested SQL agent
nodes indented) with sub-query, attempt, duration, tokens and status, then
each error or warning, and marks the first failing node with `>`. A run with no
failing node but a retry marks the first warning instead, since that is what
set the retry off.

## Replaying a trace: `nl2sql trace replay`

```bash
nl2sql --config nl2sql-demo/nl2sql.yaml trace replay 159afe1f-3305-4907-903a-55495d040665
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
- **Plan cache.** Replay makes the LLM calls the recording made. If the
  recorded planner called the model, replay turns the plan cache off so that
  call is replayed rather than answered from a plan cached since. If the
  recorded run used the cache, replay uses it too, and reports a divergence
  (`missing`) when the cached plan has since been cleared.
- **Date.** The planner and refiner prompts carry today's date, and the trace
  records it as `request.today`. Replay pins `settings.pipeline.today` to that
  date, so a trace recorded yesterday replays without diverging. A trace
  written before the date was recorded replays with the current setting.
- **Its own runtime.** The pinned date, the plan cache choice and a zero retry
  back-off go into a config derived from the engine's
  (`Runtime.derive`), and the replay runs on a runtime built from it, with
  its own playback clients. The engine's config and LLMs are never changed,
  so a query running on the same engine meanwhile is unaffected. The replayed
  result is capped with that runtime's `settings.traces` sizes, as the
  recording was.
- **Exit code**: 0 when the replayed result matches the recording, 1 on a
  divergence, a different result, or a datasource that cannot be reached.

### What replay can and cannot tell you

- Replay tests the code **downstream of the model**: parsing, validation, SQL
  generation, execution, aggregation. It is how you check a fix to that code
  against a real failure without spending tokens.
- After a **prompt change** the recorded response is stale. Replay reports the
  node where the prompt diverged; one real run is needed to record a new trace.
- The datasource must be reachable from where you replay, with the same
  configuration (the same `--config`). Replay says so and
  stops if it is not. Queries run again, so a database whose data changed
  gives a different result, and replay reports the difference.
- The datasource resolver and schema retriever still run their vector search,
  which embeds the question with the configured embedding provider (the local
  model in the demo; with `EMBEDDING_PROVIDER=openai` that is an embeddings API
  call, not a chat model call).
- Redaction applies to the recorded answers too: a model answer that
  contained something secret-shaped is replayed redacted.

## The playground

With **Debug** on, the per-node table under **Cost & time** puts a bar beside
each node's time, sized to the slowest node, so the slow step stands out. Each
node name opens that node's record from the trace: every attempt and sub-query, its
errors and warnings, what it read and returned, and for LLM nodes the exact
prompt, the raw response and the parsed result. Long text is folded. For the
`datasource_resolver` and `schema_retriever` nodes it also shows the retrieval
record as a table: the pool nearest first, picks marked with their MMR order,
dropped entries greyed, the picks in order as a row of chips, and a second row
naming the entries MMR passed over because they repeat an earlier pick. The **Retrieval** inspector at the top of the page
runs the same search for any text (see the
[demo guide](../getting_started/demo.md#the-retrieval-inspector)). The page
fetches the trace from `GET /api/trace/{trace_id}`, which reads it with
`engine.traces.get`: any id that is not a plain token is rejected before the
trace store sees it, and the `files` store reads only files directly inside its
folder, so `../` or an absolute path never reaches the filesystem.

Below the run, **Was this answer right?** records a rating keyed by the same
trace id; `nl2sql feedback stats` reads the ratings together with the traces in
`<storage.path>/traces`. See [Feedback and Signals](feedback.md).
