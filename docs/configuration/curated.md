# Curated Answers (`curated.yaml`)

`curated.yaml` is where a data team writes **verified queries**: SQL they have
already written and reviewed, with typed parameters, and the questions each
query answers.

!!! note "What works today"
    The file is loaded and checked (at startup and with
    `nl2sql curated validate`), its questions are indexed with `nl2sql index`,
    and a question is matched to a verified query and answered by it (see
    [What happens at question time](#what-happens-at-question-time)). The
    playground's *Verified* badge, a demo file and `nl2sql curated test` come
    in a later release.

## Where the file lives

| Setting | Env | Default | Meaning |
| --- | --- | --- | --- |
| `curated_config_path` | `CURATED_CONFIG` | `configs/curated.yaml` | The curated answers file, relative to the working directory or absolute. |
| `curated_enabled` | `CURATED_ENABLED` | `true` | `false` turns the feature off without removing the file: no matching, and every answer is generated. `nl2sql curated validate` still checks it. |
| `curated_match_top_k` | `CURATED_MATCH_TOP_K` | `5` | Verified query candidates a question's search returns and the model is shown, one per query. |
| `curated_match_floor` | `CURATED_MATCH_FLOOR` | `0.6` | Cosine similarity the best candidate must reach before the model is asked to choose one. Below it the question is answered as if the file did not exist, with no model call. Calibrated on the local embedding model; see [the design](../architecture/curated_answers.md#settings). |

The file is optional:

- `CURATED_CONFIG` left at its default and no `configs/curated.yaml`: the
  feature is off, and nothing else changes.
- `CURATED_CONFIG` set to any other path that does not exist: startup fails
  with `FileNotFoundError`. A typo should not quietly turn verified answers off.
- The file exists but is broken: startup (`NL2SQL(...)`, `nl2sql run`, the
  REST API, the playground) fails with a `ValueError` that names every problem,
  as a broken `datasources.yaml` does.

## Writing the file

One file covers every datasource, keyed by the datasource `id` from
`datasources.yaml`. This is the Chinook example from
[`configs/curated.example.yaml`](https://github.com/nadeem4/nl2sql/blob/main/configs/curated.example.yaml):

```yaml
version: 1
datasources:
  chinook:
    verified_queries:
      - id: top_customers_by_revenue
        description: "Customers ranked by invoiced revenue in a calendar year"
        questions:
          - "Who are our top {n} customers by revenue in {year}?"
          - "Best {n} customers in {year}"
        params:
          n:    { type: int, default: 10, min: 1, max: 100 }
          year: { type: int, min: 2000, max: 2100, example: 2012 }
        sql: |
          SELECT c.CustomerId,
                 c.FirstName || ' ' || c.LastName AS customer,
                 c.Country,
                 ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue
          FROM Customer c
          JOIN Invoice i      ON i.CustomerId = c.CustomerId
          JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
          WHERE CAST(strftime('%Y', i.InvoiceDate) AS INTEGER) = :year
          GROUP BY c.CustomerId, customer, c.Country
          ORDER BY revenue DESC, c.CustomerId
          LIMIT :n
    metrics: []
```

### A verified query

| Key | Required | Meaning |
| --- | --- | --- |
| `id` | yes | A slug: lower-case letters, digits and `_`, starting with a letter. Unique within its datasource. |
| `description` | yes | What the query returns, in a sentence. |
| `questions` | yes, at least one | Phrasings the query answers. `{name}` marks where a parameter goes. |
| `params` | no | The parameters, by name. |
| `sql` | yes | The query, in the datasource's own SQL dialect. |

### A parameter

| Key | Meaning |
| --- | --- |
| `type` | `int`, `float`, `string` or `date` (`YYYY-MM-DD`). |
| `default` | The value used when the question gives none. A param with no default is required. |
| `min`, `max` | Bounds, for `int`, `float` and `date`. |
| `choices` | The allowed values, for `string`. |
| `example` | A typical value, used where a value is needed and there is no default. |

### The SQL

- Write it in the dialect of the datasource it belongs to: the SQLite example
  above uses `strftime`, a Postgres datasource would use `EXTRACT`. It is
  parsed with the dialect the datasource's adapter declares.
- Exactly one statement, and a query: a `SELECT`, a set operation such as
  `UNION`, or a `WITH` around one. No `INSERT`, `UPDATE`, `DELETE`, `MERGE`,
  `CREATE`, `DROP`, `ALTER`, `SELECT ... INTO` or statements the parser cannot
  type (`VACUUM`, `PRAGMA`), anywhere in the tree, a CTE included.
- Parameters are `:name` placeholders, and they are exactly the declared
  `params`: no placeholder without a param, no param the SQL does not use.
  Positional `?` placeholders are refused. Values are always bound, never
  pasted into the text, so a value cannot change the shape of the query.

### Metrics

`metrics:` (a list of `name`, `description`, `expression`, `table`) is
accepted and checked for shape so the format is fixed, but nothing uses it yet.

Every key is checked: a misspelt one (`descripton:`) is an error, not ignored.

## Checking the file

```bash
nl2sql curated validate
nl2sql curated validate --curated configs/curated.yaml --config configs/datasources.yaml
```

| Option | Default |
| --- | --- |
| `--curated` | `CURATED_CONFIG` |
| `--config` | `DATASOURCE_CONFIG` |
| `--secrets` | `SECRETS_CONFIG` |

It runs every check startup runs, plus one that needs the index: every table
a query reads (CTE names aside) is in its datasource's latest indexed schema,
compared case-insensitively. A datasource that has not been indexed yet is
skipped for that check, with a note. The command prints a table, one row per
query, and exits 1 on any error, so it can run in CI:

```text
                         Verified Queries
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Datasource ┃ Query                    ┃ Status ┃ Details                            ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ chinook    │ top_customers_by_revenue │ OK     │ Verified against the indexed schema │
└────────────┴──────────────────────────┴────────┴────────────────────────────────────┘
```

| Check | At startup | `curated validate` |
| --- | --- | --- |
| YAML parses and every key is known | yes | yes |
| Every datasource key is a configured datasource | yes | yes |
| Query ids unique within a datasource | yes | yes |
| Every `{placeholder}` in a question is a param, every param is in a question | yes | yes |
| Defaults, examples, bounds and choices agree with the type | yes | yes |
| SQL is one read-only query in the datasource's dialect | yes | yes |
| SQL `:name` placeholders are exactly the params | yes | yes |
| Tables the SQL reads are in the indexed schema | no | yes, when indexed |

From Python, the same check is `CuratedAPI`, also available as
`NL2SQL(...).curated`:

```python
from nl2sql import CuratedAPI

report = CuratedAPI().validate()          # or validate(curated_path=..., datasources_path=...)
if not report.ok:
    for error in report.errors:
        print(error)
    for entry in report.entries:
        print(entry.datasource_id, entry.query_id, entry.status, entry.details)
```

## Indexing the questions

`nl2sql index` embeds every phrasing of every verified query, and its
description, beside the datasource's schema entries. Placeholders are filled
first with the param's `example`, else its `default`: "Best {n} customers in
{year}" is indexed as "Best 10 customers in 2012". The SQL is never indexed.

After editing `curated.yaml`, re-index its questions:

```bash
nl2sql index --curated-only                       # every datasource
nl2sql index --curated-only --datasource chinook  # one
```

`--curated-only` keeps each datasource's schema entries exactly as they are
and embeds only the verified queries: it reads no schema and makes no
enrichment model call, so it takes seconds. The datasource must have been
indexed once with plain `nl2sql index`. A plain `nl2sql index` also picks up
the file, and a schema re-index always keeps the verified queries.

Until you re-index, `nl2sql doctor` and the index status (`NL2SQL.index_health()`,
`GET /api/v1/index/status`) warn:

```text
Warning: curated.yaml changed for 'chinook' since it was indexed; run `nl2sql index --curated-only --datasource chinook`.
```

It is a warning, not a failure: the index stays `ok`, because the stale
entries still name queries that exist.

## What happens at question time

With the file loaded and at least one verified query in it, every question
that executes starts at the curated matcher
([node reference](../architecture/nodes/curated_matcher_node.md)):

1. It searches the indexed phrasings of the verified queries of the
   datasources the caller's role may read (only the pinned datasource when a
   request names one), and keeps the best `CURATED_MATCH_TOP_K` queries.
2. If the best one's similarity is below `CURATED_MATCH_FLOOR`, the question
   goes to the normal pipeline. No model call is made.
3. Otherwise one model call sees those candidates: each one's
   `datasource.id`, `description` and params (type, bounds, choices,
   default). Never the SQL. The model picks the one that answers the whole
   question and gives its parameter values, or answers `none_of_these`.
4. Code checks the values against the param specs. A param the question does
   not give takes its `default`; a required one missing, or a value out of
   bounds, sends the question to the normal pipeline.
5. Every table the SQL reads must be allowed for the role in
   `policies.json`. If one is not, the question goes to the normal pipeline,
   which refuses it the way it always does. The user is never told a
   verified query exists.
6. The SQL runs with the values bound by the driver, through the same
   executor as generated SQL. The row limit is the datasource's `row_limit`
   (capped by `RESULT_ROW_LIMIT`): the executor stops reading there and marks
   the result cut, rather than adding a `LIMIT` to your SQL. The result is
   saved, paged and exported like any other, and the answer is written from
   it, told what the numbers mean by the query's `description`.

A verified answer costs two model calls (the choice and the answer). When
anything above does not hold, the run continues as if the file did not
exist, and the answer is generated.

Every answer says where it came from, in `QueryResult.source` (and the REST
response):

```json
{"type": "verified", "id": "top_customers_by_revenue", "datasource_id": "chinook", "params": {"n": 5, "year": 2012}}
```

or `{"type": "generated", ...}` with the other fields `null`. The sub-query of
a verified answer has `plan_source: "verified"`, its `sql` is your SQL with
its `:name` placeholders, and its `plan` is `null`.

Why a question was not matched is in the run trace: the `curated_matcher`
node's `curated_match` record lists the candidates and their similarities, the
floor, the model's choice and reason, the checked values, and one reason code:
`not_configured`, `no_candidates`, `below_floor`, `none_of_these`,
`model_failed`, `unknown_query`, `param_invalid`, `param_missing`,
`policy_denied`, `capability_missing` or `execution_failed`. A verified query
that fails to run also adds a warning to the result.

The model for the choice is the `curatedmatcher` agent in the
[LLM config](llm.md), falling back to `default`.

Parameters are bound by the datasource's adapter. Every built-in adapter
binds them; a third-party adapter has to declare `SUPPORTS_BOUND_PARAMETERS`
([SDK](../adapters/sdk.md)), and a verified query with params on one that
does not is never run (`capability_missing`).

## Preferred tables

Preferred tables are not in `curated.yaml`: they are a `preferred_tables` list
on a datasource in `datasources.yaml`, naming views or tables (built by the
data team, never by the package) the planner should reach for first. See
[Datasources: preferred tables](datasources.md#preferred-tables).

## Source references

- Models and SQL checks: `packages/nl2sql/src/nl2sql/configs/curated.py`
- Index entries and the change hash: `packages/nl2sql/src/nl2sql/indexing/curated.py`
- Loader: `ConfigManager.load_curated()` in `packages/nl2sql/src/nl2sql/configs/manager.py`
- Startup load: `packages/nl2sql/src/nl2sql/context.py`
- Facade: `packages/nl2sql/src/nl2sql/api/curated_api.py`
- Command: `packages/nl2sql/src/nl2sql/cli/commands/curated.py`
- Matching and running: `packages/nl2sql/src/nl2sql/pipeline/nodes/curated_matcher/`
