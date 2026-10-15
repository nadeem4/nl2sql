# Cases and suites

A suite is a YAML file of cases. Each case is a question and what each role
should get. An answerable case also has the SQL a person reviewed and the
rows that SQL returns. `nl2sql.evaluation.suite` holds the model
(`EvalCase`, `EvalSuite`), the loader and the writer.

```yaml
version: 1
cases:

- id: chinook_001
  question: How many customers do we have, by country?
  datasources: [chinook]
  status: approved                  # draft | approved | retired; only approved cases run
  origin: hand                      # hand | seeded | feedback
  difficulty: easy
  tags: [aggregation, rbac-denial]
  outcome: {admin: allowed, analyst: allowed, viewer: refused}
  tables: [Customer]
  columns: [Customer.Country, Customer.CustomerId]
  order_matters: false
  sql: |
    SELECT Country, COUNT(CustomerId) AS customer_count
    FROM Customer
    GROUP BY Country
    ORDER BY customer_count DESC, Country
  rows:                             # written by regenerate, never by hand
  - {Country: USA, customer_count: 13}
  - ...
  plan: {...}                       # optional: the hand-written plan, for mode plans
```

| Field | Meaning |
| --- | --- |
| `id` | Unique in the suite |
| `question` | The natural-language question |
| `datasources` | The datasources the question is about. The `routing` scorer checks a run used exactly these. A case with `sql` names one |
| `status` | `draft`, `approved` or `retired`. Only `approved` cases run |
| `kind` | `answer` (the default, left out of the file) or `routing`: a case that says only where a question goes. A routing case has no `sql`, gets no `result_match` verdict and is left out of execution accuracy in every mode. An approved `answer` case that a role may answer must have `sql`; the loader rejects it otherwise |
| `origin` | `hand` (written by a person), `seeded` (drafted from a datasource's schema) or `feedback` (exported from a thumbs-up run) |
| `difficulty` | Optional: `easy`, `medium` or `hard` |
| `tags` | Free categories. A run can be narrowed to them, and the report gives accuracy per tag |
| `paraphrase_group` | Optional. Cases with the same intent share it and the same rows |
| `outcome` | Per role: `allowed`, `refused`, `unanswerable` or `clarify`. The case runs once per role named here |
| `tables`, `columns` | The tables the SQL reads, and the `Table.Column` names the answer needs. `retrieval_recall` scores against them |
| `order_matters` | Whether row order counts when rows are compared |
| `sql` | The reviewed SQL, in the datasource's dialect; `null` for an unanswerable case and for a `kind: routing` case |
| `rows` | The rows `sql` returns, as `{column: value}` maps. Written by `regenerate`, never typed by hand |
| `alt_sql`, `alt_rows` | [Reviewed alternative answers](../testing/evaluation-dataset.md#alternative-answers) and their rows |
| `plan` | Optional. A `PlanModel` written as data, which mode `plans` serves through its fake LLM. Mode plans needs one for every answerable case |

The outcomes:

| Outcome | What the role should get |
| --- | --- |
| `allowed` | An answer. `result_match` then checks its rows |
| `refused` | The validator's refusal, with the generic message and no rows |
| `unanswerable` | The resolver's `QUESTION_NOT_ANSWERABLE` |
| `clarify` | A request to narrow the question (status `needs_clarification`). This runs only with `engine.evals.run(clarify=True)`; Wave B adds the clarification result |

An approved case must have one `alt_rows` entry per `alt_sql`. An
unanswerable case cannot have an alternative.

## Where suites live

`evals.yaml` names suites by path, relative to `nl2sql.yaml`. The suites the
package ships are in `nl2sql/evaluation/suites/`, and `nl2sql init --demo`
copies them into the demo's `evals/` folder. `engine.evals.load(path)` and
`engine.evals.run([path])` also take a suite file directly.

The Chinook suite (`suites/chinook.yaml`) is 0.6's gold set
(`chinook_gold.yaml` and `chinook_gold_plans.yaml`) with its keys renamed and
nothing else changed:

| Was | Now |
| --- | --- |
| `datasource` | `datasources` |
| `expected` | `outcome` |
| `needed_tables`, `needed_columns` | `tables`, `columns` |
| `gold_sql`, `gold_result`, `alt_gold_sql`, `alt_gold_result` | `sql`, `rows`, `alt_sql`, `alt_rows` |
| plans in `chinook_gold_plans.yaml` | `plan` on each case |

`test_chinook_suite_holds_the_gold_data_unchanged` pins a SHA-256 of every
case's question, SQL, rows, alternatives, outcomes, tables, columns and plan,
taken from the old files with the old loader. [The Chinook
suite](../testing/evaluation-dataset.md) describes it.

## The routing suite

`suites/routing.yaml` asks which of the demo's three databases (`chinook`,
`support`, `webanalytics`) a question belongs to. Every case is `approved`,
`origin: hand`, asked as `admin`, and names the datasources a right answer
needs. None names `tables`, so in mode retrieval the `routing` scorer alone
decides it. Its tags say where each case came from:

| Tag | Cases |
| --- | --- |
| `gold` | The Chinook suite's 43 answerable questions. Their answers are checked in `chinook.yaml`; here a recall miss cannot hide a routing one |
| `sample` | Each demo datasource's sample questions, as `nl2sql init --demo` writes them |
| `paraphrase`, `hand` | Questions written for routing and read against each datasource's schema, most about `support` and `webanalytics`. Ten carry reviewed SQL, with rows written by `regenerate`, so mode full checks those answers too |

Every case without SQL is `kind: routing`, so mode full's execution accuracy
is over the ten answered cases only. The suite's header names the cases with
two defensible answers (support holds the same customers as Chinook), which a
routing change may flip on purpose.
| `cross-database` | Three questions that need two databases |

Every case also carries the names of the datasources it needs as tags, so
`--cases support` runs every case about support, cross-database ones included. Mode plans cannot run the suite, since its
cases have no plans. So the demo's `evals.yaml` names only `chinook.yaml`, and
the routing suite is run by name:

```bash
nl2sql eval run --mode retrieval --suite evals/routing.yaml --out eval_routing.json   # no key: CI's gate
nl2sql eval run --mode full --suite evals/routing.yaml --max-cost 5                   # the release check
```

In mode retrieval a case passes when routing ranks one of its datasources
first and shortlists all of them (`settings.routing.candidates`, cut at
`max_datasources`). A few cases fail today; the baseline records where
routing misses, and the gate allows no other case to start failing. The test
bed's 1,200-table wide schema has routing cases of its own in the `realdb`
job (`tests/realdb/test_routing_wide.py`), since they need the test bed's
databases.

## Regenerating rows

`rows` and `alt_rows` are never typed by hand. After you add or edit a case,
an alternative or any SQL, run:

```python
engine.evals.regenerate("evals/mine.yaml")
```

For each approved case with SQL, `regenerate` asks the case's datasource
adapter to run `sql` and every `alt_sql`. It goes through the engine's
`DatasourceRegistry`, read-only and within the datasource's limits: the
expected rows come from the same adapter questions run on. The rows are
written back in the order the SQL returns them.

The returned `RegenerateReport` counts the result sets `written`, and lists
in `failed` each case left unchanged and why:

- the SQL failed;
- the result was cut at the datasource's row limit;
- a case with SQL names more than one datasource.

A suite the package ships (`nl2sql/evaluation/suites/`) is refused unless
you pass `allow_packaged=True`. In an installed package it is not yours to
rewrite: copy it into your project, as `nl2sql init --demo` does. The repo's
own workflow passes the flag, from a checkout, after a reviewed `alt_sql` is
added to `suites/chinook.yaml`.

The other cases are still written. The file keeps its leading comment lines
and its layout, and the committed Chinook suite is byte for byte what
`regenerate` writes.

## Drafts

A case that a machine wrote starts as a `draft`, and a draft never runs.
`nl2sql eval seed` (`engine.evals.seed`) drafts cases from a datasource's
schema, with `origin: seeded` ([Benchmark your own database](seeding.md)).
`nl2sql feedback export --good` (`engine.feedback.export(path)`) writes each
thumbs-up run as a draft case to a suite file of its own, with
`origin: feedback`, the run's SQL, the datasources it read, and the rating
role allowed.

To promote a draft:

1. Check that the SQL answers the question.
2. Fill in `difficulty`, `tags`, `tables`, `columns` and the outcome for each
   role.
3. Set `status: approved`.
4. Run `regenerate` to write its rows.

Drafts never replace reviewed cases. Seeding and export refuse these paths:

- a suite the package ships;
- a suite `evals.yaml` names;
- any existing file that holds an approved case.

A file of drafts may be written again.
