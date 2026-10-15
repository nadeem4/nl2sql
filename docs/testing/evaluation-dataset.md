# The Chinook Suite

The Chinook suite holds 47 cases about the vendored Chinook database
(`nl2sql/datasets/chinook.sqlite`). Each case has hand-written SQL, the rows
that SQL returns, and a hand-written plan. Evaluations compare the engine's
answers against it. It is a suite in the [eval case format](../evals/cases.md)
and runs through [`engine.evals`](../evals/index.md).

| File | What it holds |
| --- | --- |
| `packages/nl2sql/src/nl2sql/evaluation/suites/chinook.yaml` | The suite: the cases, their rows and their plans |
| `packages/nl2sql/src/nl2sql/evaluation/suite.py` | `EvalCase`, `EvalSuite`, the loader and the writer |
| `packages/nl2sql/src/nl2sql/evaluation/regenerate.py` | `regenerate`: the rows, through the datasource's adapter |
| `packages/nl2sql/src/nl2sql/evaluation/run.py` | The three modes, prices and the cost cap |
| `packages/nl2sql/src/nl2sql/evaluation/gold_llm.py` | The gold-plan fake LLM that [mode plans](#mode-plans-gold-plans-through-the-code-nodes) runs on |
| `packages/nl2sql/src/nl2sql/evaluation/scorers/` | The scorer interface and the five built-ins |
| `packages/nl2sql/src/nl2sql/evaluation/evaluator.py` | Row comparison (`compare_results`, `compare_results_lenient`), which `result_match` reads |
| `packages/nl2sql/src/nl2sql/evaluation/faithfulness.py` | [Answer faithfulness](#answer-faithfulness): the written answer's numbers and names against the rows |
| `packages/nl2sql/src/nl2sql/evaluation/report.py` | The report's boards: accuracy, intervals, pass^k, cost, latency |
| `packages/nl2sql/src/nl2sql/evaluation/gate.py` | The regression gate's statistics: flips, McNemar's test, the checks |
| `packages/nl2sql/src/nl2sql/evaluation/prices.py` | The dated price table mode full bills every call from |
| `packages/nl2sql/src/nl2sql/evaluation/records.py` | Result records, comparable runs and the published pages |
| `packages/nl2sql/src/nl2sql/evaluation/presets/` | Named LLM configs to compare (`*.yaml`, shipped in the wheel) |
| `benchmarks/<kind>/<database>/` | Committed result records ([layout](#results-over-time)) |
| `packages/nl2sql/tests/unit/test_chinook_suite.py` | Key-free checks, part of the normal unit suite |

## The cases

The fields are described in [Cases and suites](../evals/cases.md). For this
suite:

- **The tags.** `tags` come from a fixed list: `single-table`, `filter`,
  `join`, `multi-join`, `aggregation`, `having`, `order-limit`, `offset`,
  `ties`, `date`, `paraphrase`, `keyword-in-question`, `keyword-in-value`,
  `data-range` and `value-matching`. `data-range` marks a case that needs to
  know where the data ends ("the most recent month in the data"), and
  `value-matching` one that names a stored value in other words ("heavy
  metal" for `'Heavy Metal'`).
- **`rbac-denial` and `unanswerable`.** `rbac-denial` marks exactly the cases
  some role is refused, and `unanswerable` the four the database cannot
  answer.
- **Every case is approved and hand written, about `chinook`.** So the run is
  scored, and identified, by Chinook, whatever else the demo registers.
- **The outcomes follow the demo policy.** `outcome` follows
  `CHINOOK_POLICIES` in `nl2sql/datasets/chinook.py`: a role gets `refused`
  when its scope does not allow one of `tables`, and `allowed` otherwise. An
  unanswerable case is `unanswerable` for every role.
- **Ties are broken on a key.** Where `ORDER BY` has ties, `sql` breaks them
  on a key (`CustomerId`, `Country` and so on) so the result is
  deterministic, and the case carries the `ties` tag. When the tie is only
  about order, `order_matters` is `false`.

**The data never changes.** The suite was moved from 0.6's
`chinook_gold.yaml` and `chinook_gold_plans.yaml` with only its keys renamed.
`test_chinook_suite_holds_the_gold_data_unchanged` pins a SHA-256 of every
case's question, SQL, rows, alternatives, outcomes, tables, columns and plan.
A new accepted answer is a reviewed [alternative](#alternative-answers),
followed by [`regenerate`](../evals/cases.md#regenerating-rows); never an edit
to the gold data.

## What the tests check

`pytest packages/nl2sql/tests/unit/test_chinook_suite.py` runs without an API
key and checks that:

- the data hashes to the pinned digest;
- every case is `approved` and `hand`, on `chinook`;
- ids are unique, and paraphrase groups share one result;
- every table and column exists, and `tables` equals the tables in `sql`;
- every role in `outcome` exists in the demo policy, and each outcome follows
  from it;
- every answerable case has a plan, and no other case does. Each plan is a
  `PlanModel` that reads exactly `tables` and selects as many columns as
  `rows` has;
- every `alt_sql` reads no table outside `tables`, returns between one and
  twenty-five rows, and is not the gold answer written again;
- Chinook's twelve guided questions (`CHINOOK_QUESTIONS`) are included
  verbatim. The demo offers twenty in all; the other eight ask about
  `support` and `webanalytics`, which this suite does not cover.

`tests/unit/test_eval_regenerate.py` runs `regenerate` through the sqlite
adapter and checks that the committed file is byte for byte what it writes.

## How a run is scored

Each case runs once per role in its `outcome` map, and each run is scored by
the [scorers](../evals/scorers.md). For `result_match` and `outcome`:

| Outcome | Passes when |
| --- | --- |
| `allowed` | Status `success`, one result set, and its rows equal `rows` or any [alternative](#alternative-answers) |
| `refused` | Status `error`, a `SECURITY_VIOLATION` whose message is the generic refusal (it names no table), and no rows |
| `unanswerable` | Status `error`, a `QUESTION_NOT_ANSWERABLE` from the datasource resolver's answerability check, and no rows |

Rows are compared by value, in selected column order:

- **Names don't count, order does.** Column names and aliases are ignored, so
  `SELECT Country AS c` matches a gold `Country` column, but a swapped column
  order does not.
- **Numbers match within 0.005**, because gold values are rounded to two
  decimals.
- **A number matches a string that spells it**, within the same tolerance, so
  `'2009'` is `2009`: databases differ in which of the two they return for the
  same value.
- **Other strings must be equal.** Two strings compare as strings, so
  `'2009'` is not `'2009.0'`.
- **Row order counts only when `order_matters` is true.**

### Strict and lenient accuracy

Every run is scored twice, and the report gives both, strict first.

| Score | What it is |
| --- | --- |
| **Strict** | The execution match above, unchanged: the same columns, in the same order, and the same rows |
| **Lenient** | Every gold column is answered by *some* column of the result, wherever it sits |

Strict is the honest headline. Lenient exists because most of what it
forgives is shape a person would accept:

- the same two names as `FirstName, LastName` or the other way round;
- one extra key column;
- a year labelled `2009-01-01` where the gold set writes `2009`.

Academic and vendor practice has moved the same way. Spider 2.0 counts a
prediction correct when every gold column vector appears in the result
([paper](https://arxiv.org/html/2411.07763v2)). Defog's `sql-eval` falls back
to `subset_df`, matching each gold column's values against some generated
column with names, types and order ignored
([post](https://defog.ai/blog/open-sourcing-sqleval/)).

A run passes leniently when:

- every gold column is matched, as a **multiset of values**, by a **distinct**
  column of the result, in any position, with column names ignored and the
  same numeric tolerance;
- the rows of the matched columns then line up as the strict score requires,
  so values swapped between rows still fail;
- the **row count is equal**, and row order still counts only when
  `order_matters`.

Two guardrails keep it from passing wrong answers:

- **A column cap.** The result may carry at most `max(2 x gold columns,
  gold columns + 2)` columns: three for a one-column gold answer, four for
  two, six for three. Without it a `SELECT *` would pass by carrying the gold
  columns among many others. Databricks Genie counts any extra column as bad
  ([docs](https://docs.databricks.com/aws/en/genie/benchmarks)), so a cap is
  the middle ground.
- **No constant columns.** Once a result has more than one row, a column
  whose values are all the same -- all-null included -- may not answer for a
  gold column: a hard-coded literal has the right height and tells the gold
  column nothing. On a single row every column is constant, so the rule is
  off there.

One value normalisation is added, and only one: **date and period labels**.
When a gold value is a `YYYY`, `YYYY-MM` or `YYYY-MM-DD` string and the
predicted value is a date starting with it at a component boundary, the two
are equal: `'2009'` matches `'2009-01-01'`, and `'2013-02'` matches
`'2013-02-01'`.

- Only the gold side may be the shorter label.
- Only strings are read this way: a number is a number, never a year.
- Nothing else is normalised. Anything broader belongs in an alternative
  answer, which is reviewed per case and cannot silently widen.

Lenient is a superset of strict by construction: everything strict accepts is
accepted. It is not partial credit -- a case passes a score or it does
not -- and there is no model in either score, so both stay reproducible.
Every record carries `status` and `lenient_status`, so a case that passes one
score and not the other is visible per case.

### Alternative answers

A question can have more than one right answer:

- "Who are the top 5 customers by total spend?" is answered just as well
  with the name in one column as with `FirstName` and `LastName` side by
  side.
- "What is the total revenue per year?" is answered with the year written
  `2009` or `2009-01-01`.

`alt_sql` holds those other answers, one SQL each, and `regenerate` runs each
one exactly as it runs `sql`. A run passes, strictly or leniently, when its
rows match `rows` **or any alternative**.

This is how the field handles multiple right answers:

- Snowflake has humans pick every correct answer out of several models' SQL
  ([post](https://www.snowflake.com/en/blog/engineering/cortex-analyst-text-to-sql-accuracy-bi/)).
- LinkedIn reports that about 60% of its benchmark questions now have
  several answers, and that without them it *"underreported recall by
  10-15%"*
  ([post](https://www.linkedin.com/blog/engineering/ai/practical-text-to-sql-for-data-analytics)).
- Spider 2.0 supports multiple gold files per task
  ([paper](https://arxiv.org/html/2411.07763v2)).

#### When to add one, and when not to

Prefer an alternative to loosening the comparator. An alternative is written
down, reviewed once, and only ever affects the case it sits on. A looser rule
applies to all forty-seven at once and can quietly start passing wrong
answers. The [lenient score](#strict-and-lenient-accuracy) is deliberately
narrow for the same reason.

Add one when **all** of these hold:

1. A real run produced it. Alternatives come from SQL a model actually wrote,
   not from shapes someone imagined it might write.
2. A person has **read the rows** and would accept them as an answer to the
   question as written.
3. It is the same question, from the same tables: an alternative may not read
   a table outside `tables`, and the test enforces that.
4. It is deterministic -- ties broken on a key, as `sql` does.

Do not add one when the answer is a *different* question, however reasonable
it looks. Two from the run this was built on, both refused:

- *"Where do our customers from Germany live?"* answered with three distinct
  cities. It drops the customers, so it cannot say who lives where. The
  alternative kept for `chinook_021` is the gold answer with the name in one
  column, not this.
- *"Who are the top customers by total spend?"* answered with one row. "Top
  customers" is a list with no N; one row answers "who is the top customer",
  a different question. `chinook_007` is not the same case: *"which employees
  support the **most** customers"* carries a superlative, and the one employee
  who holds the maximum is a fair reading of it, so that one is kept.

To add one:

1. Write the SQL into `alt_sql`.
2. Run `engine.evals.regenerate("evals/chinook.yaml")` to write its
   `alt_rows`.
3. Read the rows it wrote.
4. Commit both.

Never type an alternative's rows by hand.

## Mode plans: gold plans through the code nodes

`engine.evals.run(mode="plans")` checks the nodes that are plain code -- the
logical validator (including RBAC), the SQL generator and the executor --
with no model involved. Every answerable case has a hand-written `plan` (the
typed AST the planner emits) that answers it.

A local `FakeLLMServer` (`nl2sql/evaluation/gold_llm.py`) answers every LLM
call:

| Node | The fake answers |
| --- | --- |
| Datasource resolver | The case's datasources for a case with `sql`, and an empty list for an unanswerable one, so the resolver's refusal is what is scored |
| Decomposer | One sub-query whose expected columns are the plan's select aliases |
| Planner | The gold plan |
| Refiner | "Keep the same plan." |
| Answer synthesizer | A fixed sentence |

Every answer comes from one table keyed by the structured output's schema
name, so a new structured output is one more entry.

The rest is the real pipeline on the indexed demo, so a failing case is a bug
in a code node or in the plan, never in a model. The run turns curated
answers off, because a verified answer has no plan. It also scores refusals
against the generic message: the demo's config names the refused table so the
playground can show it, and the run turns that off.

Each plan reads exactly the case's `tables` (the RBAC outcome is derived from
them) and selects the rows' columns in order. PlanModel has no subqueries or
window functions, so a few plans express the answer differently from `sql`:

- anti-joins for "longest track per genre" (`chinook_008`) and "never
  purchased" (`chinook_039`);
- conditional sums in HAVING for "jazz but never rock" (`chinook_009`);
- `COUNT(DISTINCT x)` through the aggregate's `distinct: true` flag
  (`chinook_011` and `chinook_034`).

The suite file's header lists these.

Mode plans runs on every PR as `packages/nl2sql/tests/e2e/test_evals_plans.py`,
in the key-free integration job. It builds an engine on the generated demo
with no API key in the environment, runs `engine.evals.run(mode="plans")`, and
requires every case to pass -- allowed, refused and unanswerable, for every
role -- with none skipped.

When a gold plan fails, the fault is in the engine or in the plan. Fix an
engine bug with a regression test. Never change the gold data to make a plan
pass.

## Mode full: the real model, end to end

`engine.evals.run(mode="full")` runs the whole product -- every LLM node on a
real model -- on the cases, and scores it with the same scorers. It costs
money, so it never runs in CI and never runs without a dollar cap
(`max_cost_usd`). The key comes from the LLM config (`${env:...}`).

```python
from nl2sql.evaluation.presets import PRESETS_DIR, load_llm_file, model_config

report = engine.evals.run(max_cost_usd=5)                                # the engine's own llm section
report = engine.evals.run(llm={"gpt-5.4": model_config("gpt-5.4"),       # a comparison
                               "mini": load_llm_file(PRESETS_DIR / "gpt-5.4-mini-helpers.yaml")},
                          max_cost_usd=10)
```

`model_config(MODEL)` puts `MODEL` on all six LLM nodes:

- **A model in `VERIFIED_MODELS`** (`nl2sql/llm/providers.py`) names its own
  provider and temperature: `gpt-5.4`, `gpt-5.4-mini`, `gpt-4.1`, `gpt-4o`,
  `gpt-5.5`, `gpt-5-mini`, `claude-opus-5`, `claude-sonnet-5`,
  `claude-haiku-4-5`, and others. None is sent where the model rejects one
  (`gpt-5.5`, `gpt-5-mini`, `claude-opus-5`, `claude-sonnet-5`).
- **Any other model is written `provider/model`**
  (`openrouter/meta-llama/llama-3.3-70b-instruct`, `ollama/llama3`) and runs
  at temperature 0 (none for `anthropic`).

The presets in `nl2sql/evaluation/presets/` are `llm` sections, shipped in the
package:

| Preset | Models |
| --- | --- |
| `gpt-5.4` | `gpt-5.4` on every node |
| `gpt-5.4-mini-helpers` | `gpt-5.4` on the planner and refiner; `gpt-5.4-mini` elsewhere |
| `claude-planner` | `claude-opus-5` on the planner and refiner; `gpt-5.4` elsewhere |

### Cost and the cap

Every LLM call is billed from `nl2sql/evaluation/prices.py`, a committed table
of USD per 1M tokens, dated with the day it was checked:

| Model | Input | Cached input | Cache write | Output | Source |
| --- | --- | --- | --- | --- | --- |
| gpt-5.4 | 2.50 | 0.25 | 2.50 | 15.00 | OpenAI pricing page, 2026-09-21 |
| gpt-5.4-mini | 0.75 | 0.075 | 0.75 | 4.50 | OpenAI pricing page, 2026-09-21 |
| claude-opus-5 | 5.00 | 0.50 | 6.25 | 25.00 | Anthropic model table (claude-api skill, cached 2026-06-24) |
| claude-sonnet-5 | 2.00 | 0.20 | 2.50 | 10.00 | same |
| claude-haiku-4-5 | 1.00 | 0.10 | 1.25 | 5.00 | same |

A call costs `(input - cached - cache_write) x input + cached x cached_input +
cache_write x cache_write_rate + output x output`, from the token counts each
wire adapter records in `QueryResult.usage`:

- **Reasoning tokens** are part of output and billed as output.
- **Prompt caching:** OpenAI charges nothing extra to write its automatic
  cache. Anthropic bills a cache write at 1.25x input and a read at 0.1x.
- **Pricing by node:** each call is priced by the model its node is
  configured with, so a dated snapshot name in the response still prices
  correctly.

How the cap holds:

- **Before any call:** every model that any node of any config would use
  must have a price. A missing one is an `UnknownPriceError` naming the
  config, node and model.
- **Before each case:** the run adds the most expensive case so far to the
  running total, and stops if that could pass `max_cost_usd`. The cap covers
  the whole run, every config and pass.
- **The first case always runs.** So the total can pass the cap only by what
  one case costs more than the most expensive case before it.
- **A stopped run is still reported.** It returns its report with
  `stopped: "max_cost"`, and each board's `completed_cases` is short of
  `planned_cases`.

The run turns the plan cache off, so a second pass asks the planner again
instead of replaying the first. Curated answers are on unless `curated` says
otherwise: a case a verified query answered also runs with them off, and the
report's `accuracy_generated` scores every case on the generated path
([Curated answers in mode full](../evals/index.md#curated-answers-in-mode-full)).

### Answer faithfulness

Accuracy scores the rows. The answer synthesizer then writes a sentence about
them, and that sentence can state a number the rows do not hold while the
rows are right. The `faithfulness` scorer checks every written answer (its
`summary` and `content`) against the rows the run returned, deterministically,
with no model (`nl2sql/evaluation/faithfulness.py`).

| What the answer writes | Supported when |
| --- | --- |
| A number: `1,297`, `1234.5`, `$523.06`, `-5` | It equals a value in the rows at the precision written (`523.1` and `523` match `523.06`; `523.60` does not), the row count, or the sum of a numeric column |
| A percentage: `37.2%` | As above, or it is a share in the rows times 100 (`0.3718`) |
| A number inside a text cell or the question | Always: the year of `2009-01-01`, `Symphony No. 5`, `top 5`, `in 2013` |
| A quoted (`"Jazz"`) or bold (`**Rock**`) name | A text cell, a column name or the question contains it, or it contains a text cell |

Some numbers and names are never checked:

- **Not numbers:** ordinals (`2nd`), list markers (`1.` at the start of a
  line), numbers glued to a word or identifier (`chinook_001`, `MPEG-4`,
  `#7`), and the month and day of a date.
- **Names only when quoted or bold.** Bold spans with a digit, ending in `:`
  (`**Note:**`), or longer than six words are skipped, so the name check
  flags little it should not.

`1,300` for `1297`, a `2013` that neither the rows nor the question hold, and
a bold genre the rows do not list are all unsupported.

Each record carries `faithfulness`: `faithful`, `unsupported_numbers` and
`unsupported_entities` (as written), and `checked` (how many numbers and
names were checked). A refusal or error writes no answer, and the scorer does
not apply to it. The board's `faithfulness` gives the rate over the answers
written and lists each unfaithful one. Mode plans does not run it: its fake
synthesizer writes the same fixed sentence for every case.

### Reading a run: intervals, flips and pass^k

Forty-seven cases cannot settle much on their own. 27 of 47 is 57.4%, and its
95% Wilson interval is **43.3%-70.5%** -- about fourteen points either way.
Every board therefore reports that interval next to each accuracy, and the
gate below is built on the cases that moved rather than on the headline
([Anthropic, *A statistical approach to model
evals*](https://www.anthropic.com/research/statistical-approach-to-model-evals)).
The interval is taken over the runs a config scored. With more than one pass,
the runs of a single case are not independent, so it reads narrower than it
really is; pass^k below counts cases and does not.

**Compare two runs case by case, not headline to headline.** The same cases
run both times, so the runs are paired and most of the variance cancels.
`nl2sql/evaluation/gate.py` lists, per config:

- how many cases went **pass -> fail** and how many **fail -> pass**, and
  which ones;
- **McNemar's exact two-sided p-value** on those flips.

Cases both runs got right, or both got wrong, say nothing about a change.
Only the discordant pairs do, and under "the two runs are equally good" each
flip is a fair coin. Ten lost and none gained is p = 0.002; five each way is
p = 1.0.

A case counts as passed only when **every** pass passed, so over three passes
a case that has become flaky reads as a regression.

The regression gate fails a run when, for any config both reports name:

| Check | Default (`evals.yaml` `gate`) | Why |
| --- | --- | --- |
| More than `max_regressions` cases flipped from pass to fail | 2 | A handful of named cases is something a person can go and read |
| The flips are one-sided enough that McNemar's exact p < `alpha` | 0.05 | A small but real drop should not pass because it is under the count |
| Cost per case rose by more than `max_cost_increase` | 0.2 | Unchanged |

With `passes` above one the board also reports **pass^k**: the share of
cases that passed in *every* pass, for both scores, beside mean accuracy.
Mean accuracy counts runs, so a case that passes two times of three still
lifts it. pass^k counts cases, and a flaky one never counts. It is the number
to read when asking whether the engine can be relied on, rather than how
often it happens to be right ([tau-bench](https://arxiv.org/abs/2406.12045)).
`determinism` is stricter than pass^k: it says whether the SQL and rows were
identical across passes.

### Results over time

A recorded run is a JSON file under the repo's `benchmarks/` folder:

```
benchmarks/<kind>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json
```

| Part | What it is |
| --- | --- |
| `kind` | `tier2` (mode full) or `retrieval` |
| `database` | The datasource id (`chinook`) |
| `shortsha` | The first seven characters of the engine commit |
| `config` | The config name (`retrieval` for a retrieval run) |

`-2`, `-3`... are added when the name is taken.

Every record holds:

| Field | What it is |
| --- | --- |
| `schema`, `kind` | The record format (2) and the kind |
| `recorded_at` | UTC date and time |
| `engine_version` | The installed `nl2sql-engine` version (the same for every run until a release) |
| `git_commit`, `git_dirty` | The engine's commit and whether its tracked files had uncommitted changes. Found by walking up from the installed `nl2sql` package to its `.git`, so a run from the demo folder of an editable install still names the repo commit. `"unknown"` for a wheel install or without git, never null |
| `note` | What changed, e.g. `"slim prompts"` (null without one) |
| `dataset` | The suite file's name and sha256, taken with LF line endings so a Windows checkout hashes the same |
| `database` | `datasource_id`, `engine` (`sqlite`), `schema_fingerprint` and the `tables` and `columns` counts. The fingerprint hashes the latest indexed schema snapshot's tables, columns, types and keys, so re-indexing an unchanged database keeps it. Built from the datasources the suite's cases name, not every datasource the project registered: the demo registers three and this suite asks about Chinook, so a run there is still `chinook` with Chinook's fingerprint |
| `config` | The config name, and `provider:model` per node |
| `metrics` | The headline numbers: accuracy (strict and lenient) with their intervals, `pass_k`, answerability, dollars, tokens, latency, determinism and faithfulness; or, for retrieval, the recall summary |

Records are never edited after writing: a run writes a new file. Two runs are
comparable when they share the kind, the dataset sha and the schema
fingerprint, and for mode full the roles and whether curated answers were on.
`nl2sql.evaluation.records.publish()`, run from the repo root, reads every
record and rewrites [docs/benchmarks.md](../benchmarks.md) and the README's
results block. It reads no clock, so the same records always give
byte-identical pages. `packages/nl2sql/tests/unit/test_benchmark_records.py`
checks in CI, with no key, that the committed pages are exactly what
`publish` makes from the committed records.

### How mode full is tested

`packages/nl2sql/tests/e2e/test_evals_full_fake_llm.py` runs mode full with no
key and no spending. It points two `llm` sections at two `FakeLLMServer`s
that serve the gold plans with canned usage (cached and reasoning tokens
included). One server has a plan deliberately wrong. The fakes learn which
case is being asked from each run's `run.started` event, and the fake
synthesizer writes each answer from the case's rows, so the good server's
answers are faithful and the bad server's answer on its wrong rows is not.

The test checks:

- the scoring, the comparison and its differences;
- the dollars against the price table;
- the cap stopping mid-run, with a partial report;
- an unpriced model failing before any call;
- two passes with the plan cache off;
- the database identity the report records.

The unit tests cover the rest:

| Test | What it covers |
| --- | --- |
| `tests/unit/test_eval_run.py` | The runner: approved cases only, roles and selectors, the derived runtime of each mode, configs compared, curated hits run twice, clarify, the events |
| `tests/unit/test_eval_report.py` | Prices, the boards, the Wilson interval on known counts, McNemar's exact test on a constructed table, pass^k and the gate |
| `tests/unit/test_eval_scorers.py` | Each scorer |
| `tests/unit/test_answer_faithfulness.py` | The faithfulness rules |
| `tests/unit/test_benchmark_records.py` | The records |

## Retrieval recall

Before the planner sees a schema, the schema retriever picks the tables and
columns it is given. A column it never sends is one the planner cannot use.
`engine.evals.run(mode="retrieval")` measures that, per answerable case and
role, against `tables` and `columns`, with no key, no LLM and no cost.

How it runs (`nl2sql/evaluation/scorers/retrieval_recall.py`):

- **The search always runs.** Chinook's 11 tables are under
  `settings.retrieval.full_snapshot_max_tables` (15), which would send the
  whole schema without a search, so the run sets the limit to 0.
- **Each case is scored against the datasource it names**, which for this
  suite is always `chinook`, whatever else the project has registered.
  Routing's ranking is scored separately by the `routing` scorer; the
  resolver's answerability check is an LLM call and is skipped.
- **The retriever runs as the pipeline runs it** (MMR, k 8 table entries,
  then k 12 column and relationship entries of those tables), on the index
  and embedder the project has. The demo's embedder is the local ONNX
  all-MiniLM-L6-v2. Its query is the question alone, since the decomposer
  that would add filters, groupings and expected columns is an LLM.

What was sent is read from the retriever's retrieval record, the one a run
trace keeps.

The `retrieval_recall` score's `detail` has:

- `table_recall` (needed tables sent / needed tables) and `column_recall`
  (needed `Table.Column`s sent / needed);
- the `missed_tables` and `missed_columns`;
- `tables_sent` and `columns_sent`.

The board's `retrieval` has the means, and how many cases had every needed
table (`perfect_tables`) or column (`perfect_columns`). The report's
`settings` has the k values and the MMR settings, so two reports are only
compared like for like.

Read recall with the amount sent: sending all 11 tables would score 100%, so
a change that raises recall by sending more is not an improvement by itself.
A missed column is one the planner cannot select, filter or join on.

The committed baseline (2026-09-22, engine 0.1.2, 39 questions):

| Table recall | Column recall | Every table | Every column | Sent on average |
| --- | --- | --- | --- | --- |
| 98.5% | 72.6% | 37 of 39 | 15 of 39 | 8.3 tables, 24.2 columns |

Most misses are columns of a table that was sent. Once one column entry of
a table is picked, or a relationship entry adds its key columns, only those
columns are sent rather than all of them. So `Invoice` often reaches the
planner as `BillingCountry` or a key without `Total` or `InvoiceDate`
(`chinook_014`, "How much did we earn in each year?", gets `Invoice` with
`BillingCountry` only). The two table misses are `chinook_009` (`Customer`,
`Invoice`) and `chinook_034` (`Artist`).

`packages/nl2sql/tests/e2e/test_evals_retrieval.py` runs mode retrieval on the
generated demo in the key-free integration job. It checks that the run covers
every answerable case and reports recall and routing, not what the recall
is.
