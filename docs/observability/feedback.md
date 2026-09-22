# Feedback and Signals

Nothing in the engine can tell on its own whether a live answer was right. Two
small signals help: a person's rating of an answer in the playground, and the
guardrail rates the engine already records for every run. `nl2sql feedback`
reads both. Thumbs-up runs can then be exported as draft entries for the gold
set.

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
default. `FEEDBACK_ENABLED=false` turns it off: the control then says so and
`POST /api/feedback` answers `403`.

## What is stored

One row per run in a `feedback` table inside the SQLite schema store
(`SCHEMA_STORE_PATH`, `data/schema_store.db` in the demo), beside the schema
snapshots and the plan cache. No new service or file.

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

**Privacy.** A row never holds result rows, the written answer, sample values,
error messages or keys. It holds only what the run showed the role it ran as:
the SQL of a refused run is dropped, and error messages (which can name a
forbidden table) are not kept. `nl2sql feedback clear` deletes every row.

## Guardrail rates: `nl2sql feedback stats`

```bash
nl2sql --env demo feedback stats          # a table
nl2sql --env demo feedback stats --json   # the same numbers as JSON
nl2sql --env demo feedback stats --traces other/traces
```

`stats` counts every recorded run once, keyed by trace id: the feedback rows,
plus every trace in `TRACE_DIR` when `TRACE_MODE` kept them (the demo writes
`TRACE_MODE=always`). When a run has both, its counters come from the trace,
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

## Growing the gold set: `nl2sql feedback export --good`

```bash
nl2sql --env demo feedback export --good                     # feedback_gold_drafts.yaml
nl2sql --env demo feedback export --good --out drafts.yaml
```

Each thumbs-up run becomes a draft entry in the shape of
`chinook_gold.yaml` (`GoldQuestion` in `nl2sql/evaluation/gold.py`): the
question, the run's SQL as the `gold_sql` candidate, empty `tags`, `needed_tables`
and `needed_columns`, `difficulty: medium`, `expected` set to `allowed` for the
role it ran as, and no `gold_result`. A run with no SQL, or with more than one
statement, is skipped: `gold_sql` is one query. The drafts go to their own file;
`export` never writes the gold set, and refuses an `--out` that points at it.

**A person must review every draft before it joins the gold set.** A thumbs up
says an answer looked right, not that its SQL is the reference answer. Check the
SQL answers the question, fill in `difficulty`, `tags`, `needed_tables`,
`needed_columns` and `expected` (add the roles that must be refused), copy the
entry into `chinook_gold.yaml`, and run the gold generator to write its
`gold_result`:

```bash
python -m nl2sql.evaluation.gold
```

## Turning it off and clearing it

- `FEEDBACK_ENABLED=false` stops the playground recording ratings.
- `nl2sql feedback clear` deletes every rating (`--yes` skips the prompt).
  Schema snapshots and the plan cache are kept, and `nl2sql cache clear`
  never touches the ratings.
