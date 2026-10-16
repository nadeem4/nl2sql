# Settings

The `settings` section of [`nl2sql.yaml`](system.md) holds the tuning knobs.
It is optional, and every field has a default, so most configs leave it out.
Each value is the engine's own: two engines in one process can run with
different settings.

```yaml
# settings.yaml (every value shown is the default)
tenant_id: default_tenant
pipeline:
  timeout_s: 60
  plan_cache: true
  strict_columns: true
  today: null                 # pin the run date: 2013-12-31
  retries: { max: 3, base_delay_s: 1.0, max_delay_s: 10.0, jitter_s: 0.5 }
  max_llm_calls: 60           # model calls per question, parse retries included; null: no cap
  max_cost_usd: null          # dollars per question; null (the default): no cap
  sanity_checks: true
retrieval:
  full_snapshot_max_tables: 15
  table_k: 8
  planning_k: 12
  decomposer_tables: 30
routing:
  candidates: 3
  max_datasources: 3
  max_sub_queries: 6
  floor_ratio: 0.5
results:
  row_limit: 100000
  fetch_batch_size: 10000
  preview_rows: 20
traces:
  mode: on_failure            # off | on_failure | always
  sample_rows: 50
  max_field_chars: 20000
schema:
  max_versions: 3
curated:
  match_top_k: 5
  match_floor: 0.6
feedback:
  enabled: true
knowledge:                    # read from release 0.8 (Wave B)
  item_max_chars: 500
  block_max_chars: 4000
  top_k: 8
  match_floor: 0.6
clarify:
  enabled: true
profiler:
  sample_rows: 1000
  max_probes: 200
  time_budget_s: 60
  min_overlap: 0.9
  enum_max_distinct: 50
  max_cost_usd: 0.50
review:
  max_age_days: 365
```

Unknown keys are errors, as everywhere in the config. The demo
(`nl2sql init --demo`) writes a `settings.yaml` with `pipeline.timeout_s: 300`
(a live run makes six model calls; one OpenAI run took 136 s) and
`traces.mode: always` (the playground's Debug drill-down reads every run's
trace).

In Python, the groups are `Settings`, `PipelineSettings`, `RetrySettings`,
`RetrievalSettings`, `RoutingSettings`, `ResultSettings`, `TraceSettings`,
`SchemaSettings`, `CuratedSettings`, `FeedbackSettings`, `KnowledgeSettings`,
`ClarifySettings`, `ProfilerSettings` and `ReviewSettings` in `nl2sql.config`.

Where the engine's events, logs and metrics go is not a setting: it is the
[`observers` section](observers.md) of
`nl2sql.yaml`. The 0.6 `observability` group (`exporter`, `otlp_endpoint`)
is gone; an `otel` observer takes its place. The `schema` group is the attribute `schema_`
(`cfg.settings.schema_.max_versions`), because `schema` would shadow a
pydantic method; the YAML key is `schema`.

## `tenant_id`

The tenant id for requests that name none. Result files are kept per tenant.

## `pipeline`

| Key | Default | Description |
| --- | --- | --- |
| `timeout_s` | `60` | Timeout in seconds for one question. The caller is answered within about a second of the limit; the run's worker thread winds down in the background (see [isolation](../execution/isolation.md)). |
| `plan_cache` | `true` | Reuse a plan that already passed validation and executed, for the same normalised sub-query intent, datasource and schema version on the same run date, instead of calling the planner LLM. The cached plan is validated again on every use, so RBAC and policy changes still apply. Stored in `<storage.path>/state.db`; `nl2sql cache clear` empties it. See [Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture). |
| `strict_columns` | `true` | Treat a column the plan names but the retrieved schema does not have as a blocking, retryable `COLUMN_NOT_FOUND` error, so the planner is asked again instead of the plan reaching the database. `false` downgrades it to a warning that lets the plan through once retries are spent. |
| `today` | none | The date the planner and refiner are told it is, as an ISO date (`2013-12-31`). Unset, it is the real date in UTC. Pin it for tests and gold runs whose questions say "this year" or "the last 30 days". A trace records the date its run used, and `nl2sql trace replay` pins it. It is part of the plan cache key, so a plan made on another date is not reused. |
| `retries.max` | `3` | Retry attempts for the SQL agent's refinement loop. |
| `retries.base_delay_s` | `1.0` | Base delay between retries, in seconds. |
| `retries.max_delay_s` | `10.0` | Longest delay between retries. |
| `retries.jitter_s` | `0.5` | Most jitter added to a delay. |
| `max_llm_calls` | `60` | The most model calls one question may make, parse retries included; `null` turns the cap off. 60 covers the largest legitimate run: the resolver, the decomposer, six sub-queries each with a planner call and three refiner and planner retries, the answer, and parse retries. See [Per-question caps](#per-question-caps). |
| `max_cost_usd` | none | The most one question may spend, in dollars; `null` (the default) is no cap. With a cap set, every model the `llm` section names must have a [price](llm.md#prices), or the engine does not build. See [Per-question caps](#per-question-caps). |
| `sanity_checks` | `true` | The answer sanity checks after each sub-query runs (join fan-out, all-null columns, missing filter values). `false` turns them all off. Read from release 0.7's sanity checks ([Wave A](../architecture/wave_a.md#11-answer-sanity-checks)). |

### Per-question caps

Both caps are checked **before each model call**, by the question's budget:
a call that would be the 61st, or that would start once the question has
spent `max_cost_usd`, is never sent. The calls already in flight finish, so a
question can pass the cost cap by the calls in flight: at most one per
running sub-query.

A question that reaches a cap ends with `status: "stopped"`, never as a
planner error to retry: `QueryResult.stop` is `{limit, value, spent}`
(`limit` is `max_llm_calls` or `max_cost_usd`), the one error is
`RUN_LIMIT_REACHED` (not retryable) naming the limit and the setting, and the
answer says so: "This question reached its limit of 60 model calls
(settings.pipeline.max_llm_calls)." The run publishes `run.limit_reached`,
which the caller's stream carries and the [audit log](observers.md) records.

`nl2sql.limits.Budget(max_llm_calls=..., max_cost_usd=...)` is the same
check for work that is not a question: make the model calls with
`budget.callbacks()`, and a call over a cap raises `LimitReached` (with the
`RunStop` in `.stop`) before it is sent.

## `retrieval`

| Key | Default | Description |
| --- | --- | --- |
| `full_snapshot_max_tables` | `15` | When a datasource's schema has at most this many tables, the schema retriever skips vector retrieval and passes the full schema snapshot (every table, every column) to the planner. `0` always uses vector retrieval. |
| `table_k` | `8` | Table entries the schema retriever's search keeps for a sub-query (and column entries, when no table matches). |
| `planning_k` | `12` | Column and relationship entries of those tables the planner is shown for a sub-query. |
| `decomposer_tables` | `30` | Table names the decomposer is shown per datasource; the rest are counted. |

`table_k`, `planning_k` and `decomposer_tables` replace the constants the
schema retriever and the resolver had before 0.7 (`TABLE_K`, `PLANNING_K`,
`MAX_DECOMPOSER_TABLES`).

## `routing`

Cross-database routing: which datasources a question goes to, and how far it
may spread. The resolver reads `candidates`, `max_datasources` and
`floor_ratio` ([Routing](../architecture/indexing.md#routing-five-signals-fused));
the decomposer reads `max_sub_queries`
([Wave A, Limits](../architecture/wave_a.md#limits)).

| Key | Default | Description |
| --- | --- | --- |
| `candidates` | `3` | Datasources routing keeps for the answerability check, best by fused score first. |
| `max_datasources` | `3` | Datasources one question may use: of those the answerability check accepts, the resolver forwards at most this many, best first. |
| `max_sub_queries` | `6` | Sub-queries one question may have. A decomposition over it ends the run with `QUESTION_TOO_BROAD`. |
| `floor_ratio` | `0.5` | A candidate whose best index-entry similarity to the question is under this share of the most similar candidate's is dropped, between 0 and 1. It is on similarity, not on the fused score: with a handful of datasources every reciprocal-rank score is within a few percent of the best. `0` keeps every candidate up to `candidates`. |

## `results`

| Key | Default | Description |
| --- | --- | --- |
| `row_limit` | `100000` | The most rows one sub-query returns: a resource ceiling baked into the SQL as a `LIMIT`. A datasource's own `limits.row_limit` applies when it is lower (the demo datasources set `10000`). A result that reaches it is flagged as truncated (`QueryResult.truncated`, `sub_queries[].truncated`) and the answer says it was cut. |
| `fetch_batch_size` | `10000` | Rows fetched from the database and written to the sub-query's result file at a time, for an adapter that streams (every built-in SQL adapter does). At most one batch is held in memory. See [Artifact Store](../storage/artifact-store.md#batched-writes). |
| `preview_rows` | `20` | Result rows the answer model sees, with the total row count and the column names. The model writes about the result; it never copies it. See [AnswerSynthesizerNode](../architecture/nodes/answer_synthesizer_node.md). |

## `traces`

| Key | Default | Description |
| --- | --- | --- |
| `mode` | `on_failure` | When to write a run trace to `<storage.path>/traces/`: `off`, `on_failure` (errors, a retry, or an incomplete run) or `always`. See [Debugging a Run](../observability/debugging.md). |
| `sample_rows` | `50` | Result rows kept in a trace. |
| `max_field_chars` | `20000` | Longest string kept in a node's recorded inputs and outputs; LLM prompts and responses are never cut. |

## `schema`

| Key | Default | Description |
| --- | --- | --- |
| `max_versions` | `3` | The newest schema snapshots kept per datasource in the state store, besides every version a build pointer names and the one a build just replaced. |

## `curated`

| Key | Default | Description |
| --- | --- | --- |
| `match_top_k` | `5` | Verified query candidates a question's search returns, one per query. |
| `match_floor` | `0.6` | Cosine similarity the best verified query candidate must reach before the model is asked to choose one. Below it the question is answered by the normal pipeline with no model call. See [Curated Answers](curated.md#what-happens-at-question-time). |

Whether curated answers are on is not a setting: the
[`curated` section](curated.md) is present or absent.

## `feedback`

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Let the playground record a thumbs up or down, with an optional note, for a run. Stored in `<storage.path>/state.db`: question, role, status, SQL, the datasources it ran on, whether the answer was verified or generated, error codes, the model per node and the engine version, never result rows or sample values. `nl2sql feedback clear` empties it. See [Feedback and Signals](../observability/feedback.md). |

## `knowledge`, `clarify`, `profiler` and `review`

Four groups for release 0.8 ([Wave B](../architecture/wave_b.md)). They are
accepted and validated now; the Wave B change named with each reads it.

| Key | Default | Description |
| --- | --- | --- |
| `knowledge.item_max_chars` | `500` | The longest one glossary text may be; a longer one is a load error naming the item, so nothing is silently cut (K1). |
| `knowledge.block_max_chars` | `4000` | The longest the `[BUSINESS_CONTEXT]` block of a prompt may be; the lowest-scored terms, then rules, are dropped, and the trace records what was (K2). |
| `knowledge.top_k` | `8` | Matched glossary terms kept per sub-query (K2). |
| `knowledge.match_floor` | `0.6` | Similarity a term's index entry must reach to the sub-query's intent to match, between 0 and 1 (K3). |
| `clarify.enabled` | `true` | Ask which reading was meant when the planner flags a real ambiguity, instead of guessing (L1). |
| `profiler.sample_rows` | `1000` | Distinct values one join-overlap probe reads (P1). |
| `profiler.max_probes` | `200` | Overlap probes per datasource; the remaining candidates are proposed without one (P1). |
| `profiler.time_budget_s` | `60` | Seconds per datasource after which no probe starts (P1). |
| `profiler.min_overlap` | `0.9` | A measured overlap under this drops a join candidate, between 0 and 1 (P1). |
| `profiler.enum_max_distinct` | `50` | The most distinct values a column proposed as an `enum` may have (P1). |
| `profiler.max_cost_usd` | `0.50` | The most one `profile(draft=True)` call may spend drafting descriptions (P2). |
| `review.max_age_days` | `365` | Decided proposals older than this are purged by `engine.storage.purge()` (Q1). |
