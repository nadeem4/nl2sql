# Feedback and Signals

Nothing in the engine can tell on its own whether a live answer was right. Two
small signals help: a person's rating of an answer in the playground, and the
guardrail rates the engine already records for every run. `nl2sql feedback`
reads both. Thumbs-up runs can then be exported as draft entries for the gold
set, and thumbs-up generated answers as proposed verified queries.

The playground and `nl2sql feedback` are both built on `engine.feedback`
(`record`, `list`, `stats`, `export`, `clear`, `correct`), which any Python caller can use
too: see [the public facade](../api/core/public-facade.md#feedback).

## Rating an answer in the playground

After a run, below **Cost & time**, the playground asks **Was this answer
right?** with two buttons, **👍 Right** and **👎 Wrong**. A click saves the
rating at once. A note is optional: pick **Wrong number**, **Wrong table**,
**Wrong filter** or **Missing rows**, or type a few words (up to 280
characters), then **Save note**. Rating the same run again replaces the earlier
rating.

The page sends only the run's trace id, the rating and the note. The server
stores what *it* answered for that run, from its own copy of the last 200 runs,
so a page cannot put a question or SQL into the table that the engine never
produced. A run the playground did not answer is refused with `404`.

| Route | Gate |
| --- | --- |
| `GET /api/feedback` | Whether feedback is on (and why not), the counts and the last 50 ratings. The ratings are returned only where the gate below passes. |
| `POST /api/feedback` | `{"trace_id", "rating": "up" \| "down", "note"}`. Local only, the same gate as Settings, Rebuild and the Retrieval inspector: on a non-loopback `--host` it answers `403` unless `nl2sql demo --allow-settings`, and it refuses another site's page (a foreign `Origin`), a non-loopback hostname and anything but JSON. |

Feedback is available in the playground that `nl2sql demo` starts, and on by
default. `settings.feedback.enabled: false` turns it off: the control then says so and
`POST /api/feedback` answers `403`.

## What is stored

One row per run, kept by the engine's state store (`storage.state`) beside the
schema snapshots and the plan cache. The built-in `sqlite` state store keeps it
in a `feedback` table of `<storage.path>/state.db` (`nl2sql-demo/.nl2sql/state.db`
in the demo). No new service or file. The engine builds each row
(`nl2sql.feedback.rated_row`) and hands it to `StateStore.add_feedback`, so a
plugin state store keeps the same row.

| Column | Holds |
| --- | --- |
| `trace_id` | The run's id (primary key), the same id as its [trace file](debugging.md). |
| `created_at` | Unix time of the rating. |
| `rating`, `note` | `up` or `down`, and the optional note. |
| `question`, `role`, `status` | The question as asked, the role it ran as, and the result status (`success`, `plan_only`, `error`). |
| `sql` | The generated SQL per sub-query, as a JSON list. Empty for a run the role was refused (`SECURITY_VIOLATION`). |
| `error_codes` | The blocking error codes, as a JSON list. Codes only, never messages. |
| `retries`, `validator_failures`, `plan_cache_hits`, `sub_queries` | The run's guardrail counters (below). |
| `models` | The provider and model each LLM node ran on, as JSON. |
| `engine_version` | The `nl2sql-engine` version, plus the git commit when run from a checkout. |
| `datasource_ids` | The datasources the run's sub-queries ran on, each once, as a JSON list. |
| `source` | Where the answer came from: `verified` (a verified query from [`curated.yaml`](../configuration/curated.md)) or `generated`. |

`datasource_ids` and `source` were added after the table first shipped. A
`state.db` from before them gains both columns, empty, through the state
store's migrations when the engine next starts; the rows already in it keep
their ratings and read the two as `null`. See
[The state store → Migrations](../schema/store.md#migrations).

**Privacy.** A row never holds result rows, the written answer, sample values,
error messages or keys. It holds only what the run showed the role it ran as:
the SQL of a refused run is dropped, and error messages (which can name a
forbidden table) are not kept. `nl2sql feedback clear` deletes every row.

## Guardrail rates: `nl2sql feedback stats`

```bash
nl2sql feedback stats          # a table
nl2sql feedback stats --json   # the same numbers as JSON
nl2sql feedback stats --traces other/traces
```

`stats` counts every recorded run once, keyed by trace id: the feedback rows,
plus every trace in `<storage.path>/traces` when `settings.traces.mode` kept
them (the demo's config sets `always`). When a run has both, its counters come from the trace,
which also sees a validator rejection a retry then fixed, and its rating from
the row.

| Signal | Counted as |
| --- | --- |
| Thumbs up / down | Ratings; the rates are over rated runs. |
| Refused, by code | Runs that ended with `SECURITY_VIOLATION` or `QUESTION_NOT_ANSWERABLE`. |
| Refiner retried | Runs with at least one retry (`retry_count` summed over sub-queries). |
| Validator rejected a plan | Runs where the logical validator rejected a plan at least once. |
| Errored, by code | Runs with any other blocking error code. |
| Plan cache hits | Sub-queries whose plan came from the plan cache, over all planned sub-queries. |

Every other rate is over all runs. From canned data (four rated runs, three
more traces):

```text
7 runs, 4 rated (feedback table plus traces in traces)

  Signal                                 Runs             Rate
 ──────────────────────────────────────────────────────────────
  Thumbs up                                 2   50.0% of rated
  Thumbs down                               2   50.0% of rated
  Refused                                   2            28.6%
    SECURITY_VIOLATION                      1            14.3%
    QUESTION_NOT_ANSWERABLE                 1            14.3%
  Refiner retried (3 retries)               2            28.6%
  Validator rejected a plan (2 times)       2            28.6%
  Errored                                   1            14.3%
    DB_EXECUTION_ERROR                      1            14.3%
  Plan cache hits (2 of 5 sub-queries)      2            40.0%
```

`nl2sql feedback list` prints the ratings, newest first, with their notes.

## Growing an eval suite: `nl2sql feedback export --good`

```bash
nl2sql feedback export --good                     # feedback_gold_drafts.yaml
nl2sql feedback export --good --out drafts.yaml
```

Each thumbs-up run becomes a draft [eval case](../evals/cases.md), written to
a suite file of its own:

- `status: draft` and `origin: feedback`;
- the question, the run's SQL as the `sql` candidate, and the datasources it
  read;
- `outcome` set to `allowed` for the role it ran as;
- no `rows`.

A run with no SQL, or with more than one statement, is skipped: a case's
`sql` is one query. Drafts never replace reviewed cases. `export` refuses an
`--out` that is:

- a suite the package ships;
- a suite `evals.yaml` names;
- an existing file that holds an approved case.

**A person must review every draft before it runs.** A thumbs up says an
answer looked right, not that its SQL is the reference answer. To promote a
draft:

1. Check that the SQL answers the question.
2. Fill in `difficulty`, `tags`, `tables`, `columns` and `outcome` (add the
   roles that must be refused).
3. Set `status: approved`.
4. Write its rows:

```python
engine.evals.regenerate("feedback_gold_drafts.yaml")
```

## Proposing verified queries: the review queue

A thumbs-up on a *generated* answer also files a `verified_query` proposal in
the [review queue](../configuration/review.md): `engine.feedback.record(...,
rating="up")` keeps the rating and, when the run qualifies, proposes the
question and the run's SQL for `curated.yaml`. Nothing is written until an
admin accepts it:

```bash
nl2sql review list --kind verified_query --as admin
nl2sql review accept <id> --as admin
```

Runs answered by a verified query, failed runs, runs on more than one
datasource, runs with no SQL or more than one statement, and rows rated before
the table recorded the datasource file nothing. The same change is one
proposal however often it is rated; a thumbs-up therefore influences routing
only after a person accepts it. See
[From feedback to verified queries](../configuration/curated.md#from-feedback-to-verified-queries).

## Correcting an answer: `nl2sql feedback correct`

A thumbs-down says an answer was wrong; a correction also says what was right.
`engine.feedback.correct` takes the run's trace id and exactly one fix, the
right SQL or the right plan, and files it as a `verified_query` proposal
(origin `correction`) in the [review queue](../configuration/review.md):

```bash
nl2sql feedback correct 7c0e2f4a-... --sql revenue.sql --note "Revenue is invoiced, not gross." --as analyst
nl2sql feedback correct 7c0e2f4a-... --plan revenue.yaml --question "What was revenue last year?" --as analyst
```

```python
from nl2sql import UserContext

analyst = UserContext(user_id="bob", roles=["analyst"])
proposal = engine.feedback.correct(result.trace_id, sql="SELECT SUM(Total) AS revenue FROM Invoice",
                                   note="Revenue is invoiced, not gross.", user_context=analyst)
```

The fix is checked as the corrector before anything is queued, and a check
that fails raises `CorrectionRefused` (a `ValueError`) with a `code` and the
check's own `messages`; the command prints them and exits 1.

| Step | What happens | Refused as |
| --- | --- | --- |
| The run | Its kept trace gives the question (`question` overrides it) and its datasource; without a trace, its rating does. A run on several datasources is refused: a verified query answers from one. The run is rated `down` with the note unless it already is. | `no_run`, `multi_datasource` |
| A plan fix | Runs as a pinned plan with `execute=False`: `plan.after`, the logical validator and the generator, under the corrector's scope, with no model call. The SQL is the engine's rendering. | `invalid_plan`, with the validator's messages |
| A SQL fix | Must parse as exactly one read-only query in the datasource's dialect (the curated config's own check), with no `:name` placeholders, and read only tables the corrector's role may read. | `not_read_only`, `denied` |
| The hooks | The SQL passes the `execute.before` hooks with the corrector as the caller. A deny or a failed hook refuses it, and so does a request for approval: a correction is never run with an approval. | `hook_denied`, `needs_approval` |
| The check | The SQL runs once through the adapter, as `nl2sql curated test` runs a verified query: read-only, under the datasource's limits, reading the first batch of rows and keeping none. An empty result is allowed. | `failed`, with the database's message |
| The proposal | `change` is in `curated.yaml`'s shape: an id slugged from the question, the question as its one phrasing, the SQL, and the note as its description (a placeholder without one). `tables` lists every table the fix and the original SQL read; `evidence` holds the trace id, the original SQL, `ran`, `row_count` and `columns`. | |

Nothing is written to `curated.yaml` (or any file a person owns) until an
admin accepts the proposal, usually with edits: a better id, more phrasings,
and `:name` params in place of the literal values. Only a caller whose scope
reaches every table in `tables` sees the proposal. Keep traces
(`settings.traces.mode: always`) to correct runs that succeeded and were not
rated; the default keeps only failed runs' traces.

## Turning it off and clearing it

- `settings.feedback.enabled: false` stops the playground recording ratings.
- `nl2sql feedback clear` deletes every rating (`--yes` skips the prompt).
  Schema snapshots and the plan cache are kept, and `nl2sql cache clear`
  never touches the ratings.
