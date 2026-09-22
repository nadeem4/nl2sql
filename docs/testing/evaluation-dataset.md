# Chinook Gold Evaluation Dataset

A set of about forty questions about the vendored Chinook database
(`nl2sql/datasets/chinook.sqlite`), each with hand-written SQL and the rows
that SQL returns. It is the reference an evaluation compares the engine's
answers against.

| File | What it holds |
| --- | --- |
| `packages/nl2sql/src/nl2sql/evaluation/datasets/chinook_gold.yaml` | The dataset |
| `packages/nl2sql/src/nl2sql/evaluation/gold.py` | `GoldQuestion` model (with `alt_gold_sql`), `load_gold_dataset()`, `execute_gold_sql()`, `regenerate()` |
| `packages/nl2sql/src/nl2sql/evaluation/datasets/chinook_gold_plans.yaml` | A hand-written `PlanModel` per answerable question, for [tier 1](#tier-1-gold-plans-through-the-code-nodes) |
| `packages/nl2sql/src/nl2sql/evaluation/tier1.py` | `load_gold_plans()`, the gold-plan fake LLM, `run_tier1()` |
| `packages/nl2sql/src/nl2sql/evaluation/tier2.py` | [Tier 2](#tier-2-the-real-model-end-to-end): the cost cap, the scoreboard, the comparison and the baseline check |
| `packages/nl2sql/src/nl2sql/evaluation/faithfulness.py` | [Answer faithfulness](#answer-faithfulness): the written answer's numbers and names against the rows |
| `packages/nl2sql/src/nl2sql/evaluation/retrieval_recall.py` | [Retrieval recall](#retrieval-recall): table and column recall of schema retrieval, no key |
| `packages/nl2sql/src/nl2sql/evaluation/prices.py` | The dated price table tier 2 bills every call from |
| `packages/nl2sql/src/nl2sql/evaluation/records.py` | Result records, comparable runs, `nl2sql benchmark publish` and `--from` |
| `packages/nl2sql/src/nl2sql/evaluation/presets/` | `--model` configs and the built-in `--llm` presets (`*.yaml`, shipped in the wheel) |
| `packages/nl2sql/src/nl2sql/evaluation/baselines/` | Where a committed tier 2 baseline scoreboard goes (none yet) |
| `benchmarks/<kind>/<database>/` | Committed result records: `tier2/` one per run and config, `retrieval/` one per run ([layout](#results-over-time)) |
| `packages/nl2sql/src/nl2sql/evaluation/evaluator.py` | Row comparison (`compare_results`, `compare_results_lenient`) and per-role scoring (`score_case`, `score_case_lenient`) |
| `packages/nl2sql/tests/unit/test_chinook_gold_dataset.py` | Key-free checks, part of the normal unit suite |

## Format

The file is a YAML list. Each entry:

| Field | Meaning |
| --- | --- |
| `id` | Unique id, `chinook_NNN` |
| `question` | The natural-language question |
| `difficulty` | `easy`, `medium` or `hard` |
| `tags` | Categories: `single-table`, `filter`, `join`, `multi-join`, `aggregation`, `having`, `order-limit`, `ties`, `date`, `paraphrase`, `keyword-in-question`, `keyword-in-value`, `rbac-denial`, `unanswerable` |
| `paraphrase_group` | Optional. Questions with the same intent share this id and the same `gold_result`, for determinism checks |
| `datasource` | The database the question is asked against. Defaults to `chinook` and is written out only where a question differs, so the file - and its sha256 - are unchanged. It is what the run is scored and identified by, so the demo's other databases do not widen it |
| `needed_tables` | Exactly the tables `gold_sql` reads, for retrieval recall |
| `needed_columns` | `Table.Column` names the answer needs, for retrieval recall |
| `expected` | Per demo role (`admin`, `analyst`, `viewer`): `allowed`, `refused` or `unanswerable` |
| `order_matters` | Whether a comparison should respect row order |
| `gold_sql` | Hand-written SQLite SQL, or `null` for an unanswerable question |
| `gold_result` | Rows `gold_sql` returns, as a list of `{column: value}` maps. Generated, never typed. `null` when `gold_sql` is `null` |
| `alt_gold_sql` | Optional. [Reviewed alternative answers](#alternative-gold-answers) to the same question, each hand-written SQLite SQL |
| `alt_gold_result` | The rows each `alt_gold_sql` returns, in the same order. Generated, never typed. Omitted with `alt_gold_sql` |

`expected` follows the demo policy in `nl2sql/cli/demo/chinook.py`
(`CHINOOK_POLICIES`): a role gets `refused` when any of `needed_tables` is
outside its `allowed_tables`, and `allowed` otherwise. Questions the database
cannot answer are `unanswerable` for every role. The `rbac-denial` tag marks
exactly the questions some role is refused.

Where `ORDER BY` has ties, `gold_sql` breaks them on a key (`CustomerId`,
`Country` and so on) so the result is deterministic, and the question carries
the `ties` tag. When the tie is only about order, `order_matters` is `false`.

## Regenerating the results

`gold_result` and `alt_gold_result` are always produced by running `gold_sql`
and each `alt_gold_sql` against the database. After adding or editing a
question, an alternative or any SQL, set its `gold_result` to `null` (or leave
it stale) and run:

```bash
python -m nl2sql.evaluation.gold
```

It re-executes every `gold_sql` and every `alt_gold_sql`, rewrites the file in
its canonical layout and prints how many results it wrote. A question with no
alternatives carries neither field. Commit the rewritten file.

## What the tests check

`pytest packages/nl2sql/tests/unit/test_chinook_gold_dataset.py` runs without
an API key and checks that:

- every entry validates against `GoldQuestion` and ids are unique;
- every `gold_sql` reproduces its committed `gold_result`, and the committed
  file is byte-for-byte what the generator writes;
- every `alt_gold_sql` reproduces its committed `alt_gold_result` the same
  way, reads no table outside `needed_tables`, returns between one and
  twenty-five rows, and is not the gold answer written again;
- paraphrase groups share one result;
- every needed table and column exists, and `needed_tables` equals the tables
  in `gold_sql`;
- every role in `expected` exists in the demo policy and each outcome follows
  from it;
- Chinook's twelve guided questions (`CHINOOK_QUESTIONS`) are included
  verbatim. The demo offers twenty in all (`DEMO_QUESTIONS`); the other eight
  ask about `support` and `webanalytics`, which this Chinook-only gold set does
  not cover.

## Running the benchmark

```bash
nl2sql --env demo benchmark --tier 1        # gold plans, no API key
nl2sql --env demo benchmark --tier 2 --max-cost 5   # the real model, scored and priced
nl2sql --env demo benchmark                 # full pipeline, the configured LLM
nl2sql --env demo benchmark retrieval       # schema retrieval recall, no API key
```

Each question runs once per role in its `expected` map (narrow with
`--include-ids` and `--role`; tier 2 runs as `admin` unless `--role` says
otherwise), and each run is scored:

| Expected | Passes when |
| --- | --- |
| `allowed` | Status `success`, one result set, and its rows equal `gold_result` or any [alternative](#alternative-gold-answers) |
| `refused` | Status `error`, a `SECURITY_VIOLATION` whose message is the generic refusal (it names no table), and no rows |
| `unanswerable` | Status `error`, a `QUESTION_NOT_ANSWERABLE` from the datasource resolver's answerability check, and no rows |

Rows are compared by value, in selected column order: column names and
aliases are ignored, so `SELECT Country AS c` matches a gold `Country`
column but a swapped column order does not. Numbers match within 0.005,
because gold values are rounded to two decimals. A number also matches a
string that spells one, within the same tolerance, so `'2009'` is `2009`:
databases differ in which of the two they return for the same value. Other
strings must be equal, and two strings compare as strings (`'2009'` is not
`'2009.0'`). Row order counts only when `order_matters` is true.

### Strict and lenient accuracy

Tier 2 scores every run twice and reports both, strict first.

| Score | What it is |
| --- | --- |
| **Strict** | The execution match above, unchanged: the same columns, in the same order, and the same rows |
| **Lenient** | Every gold column is answered by *some* column of the result, wherever it sits |

Strict is the honest headline. Lenient exists because most of what it
forgives is shape a person would accept: the same two names as
`FirstName, LastName` or the other way round, one extra key column, a year
labelled `2009-01-01` where the gold set writes `2009`. Academic and vendor
practice has moved the same way -- Spider 2.0 counts a prediction correct
when every gold column vector appears in the result
([paper](https://arxiv.org/html/2411.07763v2)) and Defog's `sql-eval` falls
back to `subset_df`, matching each gold column's values against some
generated column with names, types and order ignored
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
are equal -- `'2009'` matches `'2009-01-01'`, `'2013-02'` matches
`'2013-02-01'`. Only the gold side may be the shorter label, only strings are
read this way (a number is a number, never a year), and nothing else is
normalised. Anything broader belongs in an alternative gold answer, which is
reviewed per question and cannot silently widen.

Lenient is a superset of strict by construction: everything strict accepts is
accepted. It is not partial credit -- a question passes a score or it does
not -- and there is no model in either score, so both stay reproducible.

Every result row carries `status` and `lenient_status`, so a question that
passes one score and not the other is visible per question; the printed
table shows a **Strict** and a **Lenient** column beside each other, and the
scoreboard, the records, the README block and
[docs/benchmarks.md](../benchmarks.md) all report both.

### Alternative gold answers

A question can have more than one right answer. "Who are the top 5 customers
by total spend?" is answered just as well with the name in one column as with
`FirstName` and `LastName` side by side; "What is the total revenue per year?"
is answered with the year written `2009` or `2009-01-01`. `alt_gold_sql` holds
those other answers, one SQL each, executed and committed exactly as
`gold_sql` is. A run passes -- strictly or leniently -- when its rows match the
gold answer **or any alternative**.

This is how the field handles multiple right answers. Snowflake has humans
pick every correct answer out of several models' SQL
([post](https://www.snowflake.com/en/blog/engineering/cortex-analyst-text-to-sql-accuracy-bi/));
LinkedIn reports that about 60% of its benchmark questions now have several
answers and that without them it *"underreported recall by 10-15%"*
([post](https://www.linkedin.com/blog/engineering/ai/practical-text-to-sql-for-data-analytics));
Spider 2.0 supports multiple gold files per task
([paper](https://arxiv.org/html/2411.07763v2)).

#### When to add one, and when not to

Prefer an alternative to loosening the comparator. An alternative is written
down, reviewed once, and only ever affects the question it sits on; a looser
rule applies to all forty-three at once and can quietly start passing wrong
answers. The [lenient score](#strict-and-lenient-accuracy) is deliberately
narrow for the same reason.

Add one when **all** of these hold:

1. A real run produced it. Alternatives come from SQL a model actually wrote,
   not from shapes someone imagined it might write.
2. A person has **read the rows** and would accept them as an answer to the
   question as written.
3. It is the same question, from the same tables: an alternative may not read
   a table outside `needed_tables`, and the test enforces that.
4. It is deterministic -- ties broken on a key, as `gold_sql` does.

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

Write the SQL into `alt_gold_sql`, run `python -m nl2sql.evaluation.gold` to
generate `alt_gold_result`, read the rows it wrote, and commit both. Never
type an alternative's rows by hand.

The command prints one row per question and role, then a pass/fail/skip/xfail
count per role, writes the whole report as JSON (`--export-path`, default
`benchmark_report.json`) and exits 1 if any case failed. Without `--tier`,
`--bench-config-path` names a YAML map of LLM configs to run in turn; without
it the context's own LLM config runs once.

## Tier 1: gold plans through the code nodes

Tier 1 checks the nodes that are plain code -- the logical validator
(including RBAC), the SQL generator and the executor -- with no model
involved. `chinook_gold_plans.yaml` holds, for every answerable question, a
hand-written `PlanModel` (the typed AST the planner emits) that answers it. A
local `FakeLLMServer` answers every LLM call: the datasource resolver's
answerability check gets `["chinook"]` for a question with `gold_sql` and an
empty list for an unanswerable one (so the resolver's refusal is what is
scored), the decomposer gets one
sub-query on `chinook` whose expected columns are the plan's select aliases,
the planner gets the gold plan, the refiner "Keep the same plan." and the
answer synthesizer a fixed sentence. The rest is the real pipeline on the
indexed demo, so a failing case is a bug in a code node or in the plan, never
in a model. Refusals are scored against the default, generic message: the
demo's `.env.demo` sets `RBAC_REFUSAL_NAMES_TABLES=true` so the playground
can show which table was refused, and tier 1 turns that off for its run.

Each plan reads exactly the question's `needed_tables` (the RBAC outcome is
derived from them) and selects the gold result's columns in order. PlanModel
has no subqueries or window functions, so a few plans express the answer
differently from `gold_sql`: anti-joins for "longest track per genre"
(`chinook_008`) and "never purchased" (`chinook_039`), and conditional sums
in HAVING for "jazz but never rock" (`chinook_009`). The header of the plans
file lists these. `chinook_011` and `chinook_034` use `COUNT(DISTINCT x)`
through the aggregate's `distinct: true` flag.

Tier 1 runs on every PR as `packages/nl2sql/tests/e2e/test_benchmark_tier1.py`,
in the key-free integration job: it generates the demo, runs
`nl2sql benchmark --tier 1` with no API key in the environment, and requires
every case -- allowed, refused and unanswerable, for every role -- to pass,
with none skipped. `packages/nl2sql/tests/unit/test_tier1_gold_plans.py`
checks in the unit job that every answerable question has a plan, that it
reads exactly the needed tables and selects as many columns as the gold
result, and that the answerability verdict served follows the gold dataset.

When a gold plan fails, the fault is in the engine or in the plan. Fix an
engine bug with a regression test; do not change the gold data to make a plan
pass unless the gold data is itself wrong.

## Tier 2: the real model, end to end

Tier 2 runs the whole product -- every LLM node on a real model -- on the gold
questions and scores it with the same runner and scoring as tier 1. It costs
money, so it never runs in CI (it refuses when `CI` is set) and never runs
without a dollar cap. The key comes from the environment or the LLM config
(`${env:...}`), never from the command line; traces follow `TRACE_MODE` as for
any run.

The commands the owner runs, from the demo folder:

```bash
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5                        # a run
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --llm mini-helpers --max-cost 10    # a comparison
```

and then, from the repo, `nl2sql benchmark publish --from <demo folder>`
([results over time](#results-over-time)).

| Option | Default | Meaning |
| --- | --- | --- |
| `--max-cost USD` | required | Stop before a question that could take the run's total spend past this |
| `--model MODEL` | none | A model to put on every LLM node, named in the scoreboard by itself (repeatable; below) |
| `--llm SPEC` | none | An LLM config to compare (repeatable): a preset name, `PATH.yaml` (named by the file stem) or `NAME=PATH`. With no `--model` or `--llm`, the project's LLM config runs as `default` |
| `--note TEXT` | none | A short label for what changed, e.g. `"slim prompts"`, kept in each record and shown in the history |
| `--role ROLE` | `admin` | Run as this role (repeatable) |
| `--passes N` | `1` | Run every question N times per config and report determinism and pass^N. Use `--passes 3` for a baseline |
| `--questions ID_OR_TAG` | every question | Only these question ids or tags (repeatable or comma-separated), e.g. `--questions unanswerable,chinook_001` |
| `--export-path PATH` | `<project>/benchmark_tier2.json` | Where to write the full scoreboard |
| `--results-dir DIR` | `<project>/benchmarks` | The benchmarks folder; one record per config goes in `DIR/tier2/<database>/` |
| `--baseline PATH` | none | A committed scoreboard to check against (below) |
| `--max-regressions N` | `2` | Baseline: how many questions may flip from pass to fail before the run fails; a smaller but significant drop fails too |
| `--max-accuracy-drop F` | none | Deprecated: a flat accuracy drop gate. Smaller than one question at n = 43, so it fired on noise. Only applied when passed |
| `--max-cost-increase F` | `0.2` | Baseline: largest allowed rise in cost per question (0.2 is 20%) |

`<project>` is the folder of the env file the run is configured from: for
`--env demo` that is `./.env.demo`, so the current folder; with `--env-file
PATH` it is that file's folder.

The option is `--llm`, not `--config`: on every `nl2sql` command `--config` is
the datasource config and `--llm-config` the single LLM config path.

Before anything is spent, the run prints its plan and carries on without a
prompt (the cap is the safety):

```
Plan: 43 questions x role admin x 1 pass, cap $10.00
  gpt-5.4: gpt-5.4 (every node)
  gpt-5.4-mini-helpers: gpt-5.4 (astplanner, refiner); gpt-5.4-mini (answersynthesizer, datasourceresolver, decomposer)
  Records: C:\demo\benchmarks\tier2  Scoreboard: C:\demo\benchmark_tier2.json
```

Exit codes: 0 when every config ran, 1 on a baseline regression or a failed
run, 2 on a usage error (no `--max-cost`, an unknown `--model` or `--llm`, two
configs with one name, `CI` set), 3 when the cap stopped the run early.

### `--model` and the presets

`--model MODEL` puts `MODEL` on all five LLM nodes. A model in
`VERIFIED_MODELS` (`nl2sql/llm/providers.py`: `gpt-5.4`, `gpt-5.4-mini`,
`gpt-4.1`, `gpt-4o`, `gpt-5.5`, `gpt-5-mini`, `claude-opus-5`,
`claude-sonnet-5`, `claude-haiku-4-5`, ...) names its provider and its
temperature: none is sent where the model rejects one (`gpt-5.5`,
`gpt-5-mini`, `claude-opus-5`, `claude-sonnet-5`). Any other model is written
`provider/model` (`openrouter/meta-llama/llama-3.3-70b-instruct`,
`ollama/llama3`) and runs at temperature 0 (none for `anthropic`); a bare
unknown model is a usage error. The key is the provider's `${env:...}`
variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`), which
`--env demo` loads from `.env.demo`. No `base_url` is written, so the provider
preset's endpoint is used.

`--llm NAME` picks a preset, a config file shipped inside the package.
`nl2sql benchmark presets` lists them:

```
Tier 2 presets (--llm NAME):
  claude-planner: claude-opus-5 (astplanner, refiner); gpt-5.4 (answersynthesizer, datasourceresolver, decomposer)
  gpt-5.4: gpt-5.4 (every node)
  gpt-5.4-mini-helpers: gpt-5.4 (astplanner, refiner); gpt-5.4-mini (answersynthesizer, datasourceresolver, decomposer)
```

A preset matches by its full name or a `-`-separated suffix, so `--llm
mini-helpers` is `gpt-5.4-mini-helpers`, and the scoreboard names it by its
full name. `claude-planner` needs the `anthropic` extra and
`ANTHROPIC_API_KEY`. `--llm PATH.yaml` runs your own file under its stem, and
`--llm NAME=PATH` under `NAME`. `--model` and `--llm` combine: every one
becomes a config in one comparison, `--model`s first.

### Comparing configs

Each config runs on the same questions, one after another, and the
scoreboard puts them side by side and lists every question one config passed
and another failed (with `--passes` > 1 a question can also be `flaky`). Since
[per-node providers](../configuration/llm.md), a config can put each LLM node
(`datasourceresolver`, `decomposer`, `astplanner`, `refiner`,
`answersynthesizer`) on its own provider and model, as the presets do:

```bash
nl2sql --env demo benchmark --tier 2 --max-cost 10 --model gpt-5.4 --llm mini-helpers --llm claude-planner
```

A model that is not in `VERIFIED_MODELS` (`nl2sql/llm/providers.py`) gets
a warning: the engine's parameters have not been checked against it.

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
wire adapter records in `QueryResult.usage`. Reasoning tokens are part of
output and billed as output. OpenAI charges nothing extra to write its
automatic cache; Anthropic bills a cache write at 1.25x input and a read at
0.1x. Each call is priced by the model its node is configured with, so a dated
snapshot name in the response still prices correctly.

Before any call, every model any LLM node of any config would use must have a
price; a missing one is an error naming the config, node and model. Before
each question the run adds the dearest question seen so far to the running
total and stops if that could pass `--max-cost`. The cap covers the whole run
(every config and pass), and the first question always runs, so the total can
exceed the cap only by what one question costs more than the dearest before
it. The running spend prints after every question. A stopped run still writes
its scoreboard, marked `"stopped": "max_cost"`, with `completed_cases` short
of `planned_cases`.

Tier 2 turns the plan cache off for its run (`PLAN_CACHE_ENABLED`), so a
second pass asks the planner again instead of replaying the first, and scores
refusals against the generic message, as tier 1 does.

### The scoreboard

`benchmark_tier2.json` holds the run (`passes`, `roles`, `questions`,
`max_cost`, `spent`, `stopped`, `prices_checked_on`), one entry per config
under `configs`, and a `comparison`. Each config has:

| Field | What it is |
| --- | --- |
| `models` | `provider:model` per LLM node |
| `planned_cases`, `completed_cases` | Cases the run would make, and made |
| `summary` | pass/fail counts per role, as tier 1 reports |
| `accuracy` | `overall` (strict) and `lenient`, each with a 95% Wilson `interval`; `pass_k` when `--passes` > 1; then `by_tag` and `by_difficulty`, each with both counts and both rates |
| `answerability` | `precision` and `recall` of refusing as unanswerable: true refusals of the four unanswerable questions, false refusals of answerable ones, and missed unanswerables |
| `tokens_by_node` | calls, input, cached input, cache write, output and reasoning tokens per node |
| `cost` | dollars `total` and `per_question` (each result row has its own `cost`) |
| `latency` | p50 and p95 seconds per question, and per node |
| `retries` | refiner retries in total, and questions that needed one |
| `errors_by_code` | error codes the runs ended with (`EXCEPTION` for a run that raised) |
| `determinism` | with `--passes` > 1: the share of questions whose SQL and rows were identical in every pass, and which ones differed |
| `faithfulness` | [answer faithfulness](#answer-faithfulness): `faithful` of `answers` written, the `rate`, and each `unfaithful` run with what it stated that the rows do not hold |
| `results` | one row per run: `status` and `reason` (strict), `lenient_status` and `lenient_reason`, SQL, cost, latency, tokens, retries, `faithfulness` (`null` when no answer was written), and `plans`: each sub-query's `id`, `intent` and `plan` (the `PlanModel` JSON the SQL was generated from, `null` if planning failed), so a wrong answer can be traced to the plan; and `answer`, the text the answer synthesizer wrote (summary, then content), which the faithfulness check read |

`comparison.configs` is one row per config (strict and lenient accuracy, answerability, cost,
latency, retries, determinism, faithfulness); `comparison.differences` lists
the questions the configs disagree on. The command prints the same as tables:

```
              Tier 2 scoreboard (accuracy with its 95% interval)
Config        Cases  Strict             Lenient            Ans. P  Ans. R  Cost     $/question  p50    p95    Retries  Determinism  Faithful
gpt-5.4       8/8    100.0% [67.6-100]  100.0% [67.6-100]  100.0%  100.0%  $0.0806  $0.0101     0.08s  0.25s  0        100.0%       100.0%
mini-helpers  8/8    75.0% [40.9-92.9]  87.5% [52.9-97.8]  100.0%  100.0%  $0.0372  $0.0046     0.09s  0.09s  0        100.0%       85.7%

                  Questions the configs disagree on
ID           Role   gpt-5.4  mini-helpers
chinook_018  admin  pass     fail

              Answers stating what the rows do not hold
Config        ID           Role   Pass  Unsupported
mini-helpers  chinook_018  admin  1     MPEG audio file
```

(a run against the fake LLM, with one plan deliberately wrong on the second
server.)

### Answer faithfulness

Accuracy scores the rows. The answer synthesizer then writes a sentence about
them, and that sentence can state a number the rows do not hold while the
rows are right. Tier 2 checks every written answer (its `summary` and
`content`) against the rows the run returned, deterministically, with no
model (`nl2sql/evaluation/faithfulness.py`). It never changes pass or fail.

| What the answer writes | Supported when |
| --- | --- |
| A number: `1,297`, `1234.5`, `$523.06`, `-5` | It equals a value in the rows at the precision written (`523.1` and `523` match `523.06`; `523.60` does not), the row count, or the sum of a numeric column |
| A percentage: `37.2%` | As above, or it is a share in the rows times 100 (`0.3718`) |
| A number inside a text cell or the question | Always: the year of `2009-01-01`, `Symphony No. 5`, `top 5`, `in 2013` |
| A quoted (`"Jazz"`) or bold (`**Rock**`) name | A text cell, a column name or the question contains it, or it contains a text cell |

Not read as numbers: ordinals (`2nd`), list markers (`1.` at the start of a
line), numbers glued to a word or identifier (`chinook_001`, `MPEG-4`, `#7`),
and the month and day of a date. Names are only checked when quoted or bold,
and bold spans with a digit, ending in `:` (`**Note:**`) or longer than six
words are skipped, so the name check flags little it should not. `1,300` for
`1297`, `2013` when neither the rows nor the question hold it, and a bold
genre the rows do not list are unsupported.

Each result row carries `faithfulness`: `faithful`, `unsupported_numbers` and
`unsupported_entities` (as written) and `checked` (how many numbers and names
were checked). A refusal or error writes no answer and is `null`. Read the
rate with the list: an unfaithful answer is a synthesizer problem to look at,
not a failed question. Tier 1 does not report it: its fake synthesizer writes
the same fixed sentence for every question, with nothing to check.

### Reading a run: intervals, flips and pass^k

Forty-three questions cannot settle much on their own. 25 of 43 is 58.1%, and
its 95% Wilson interval is **43.3%-71.6%** -- about fourteen points either
way. Every run therefore prints and records that interval next to both
scores, written `58.1% [43.3-71.6]`, and the gate below is built on the
questions that moved rather than on the headline
([Anthropic, *A statistical approach to model
evals*](https://www.anthropic.com/research/statistical-approach-to-model-evals)).
The interval is taken over the runs a config scored, so with `--passes` above
one the runs of a single question are not independent and it reads narrower
than it really is; pass^k below counts questions and does not.

**Compare two runs question by question, not headline to headline.** The same
43 questions are run both times, so the runs are paired and most of the
variance cancels. `--baseline PATH` prints, per config:

- the strict and lenient accuracy of each run, side by side;
- how many questions went **pass -> fail** and how many **fail -> pass**, and
  which ones;
- **McNemar's exact two-sided p-value** on those flips. Questions both runs
  got right, and both got wrong, say nothing about a change; only the
  discordant pairs do, and under "the two runs are equally good" each flip is
  a fair coin. Ten lost and none gained is p = 0.002; five each way is p = 1.0.

A question counts as passed only when **every** pass passed, so with
`--passes 3` a question that has become flaky reads as a regression.

`docs/benchmarks.md` carries the same thing in its "Δ vs previous" column:
the headline deltas, then `3 flipped to fail, 1 to pass (McNemar p=0.625)`.
The README block keeps the deltas only.

#### The regression gate

`--baseline` exits 1 when, for any config both scoreboards name:

| Check | Default | Why |
| --- | --- | --- |
| More than `--max-regressions` questions flipped from pass to fail | 2 | A handful of named questions is something a person can go and read |
| The flips are one-sided enough that McNemar's exact p < 0.05 | - | A small but real drop should not pass because it is under the count |
| Cost per question rose by more than `--max-cost-increase` | 0.2 | Unchanged |

`--max-accuracy-drop` is **deprecated**. It failed a run when accuracy fell by
more than a flat fraction, two points by default -- and at n = 43 one question
is 2.3 points, so the gate was tighter than the resolution of the benchmark
and fired on run-to-run noise. It still works when it is passed explicitly,
with a warning, and is no longer applied unless it is.

#### `--passes` and pass^k

`--passes` is 1 for an ordinary run: one pass is enough to see where the
engine stands, and every call costs money. Run a **baseline** with
`--passes 3`, so the run it is compared against is not a single sample:

```bash
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --passes 3 --max-cost 15 \
  --export-path <repo>/packages/nl2sql/src/nl2sql/evaluation/baselines/gpt-5.4.json
```

With more than one pass the run also reports **pass^k**: the share of
questions that passed in *every* pass, for both scores, beside mean accuracy.
Mean accuracy counts runs, so a question that passes two times of three still
lifts it; pass^k counts questions, and a flaky one never counts. It is the
number to read when asking whether the engine can be relied on rather than
how often it happens to be right ([tau-bench](https://arxiv.org/abs/2406.12045)).
`determinism` stays what it was: whether the SQL and rows were identical
across passes, which is a stricter thing than passing every time.

#### Where baselines live

`packages/nl2sql/src/nl2sql/evaluation/baselines/`; none is committed yet. A
run is compared config by config, by name, and a config the baseline lacks is
skipped:

```bash
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5 \
  --baseline <repo>/packages/nl2sql/src/nl2sql/evaluation/baselines/gpt-5.4.json
```

### Results over time

Every tier 2 run writes one record per config, and `benchmark retrieval
--record` one record, under the project's `benchmarks/` folder:

```
benchmarks/<kind>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json
```

`kind` is `tier2` or `retrieval`, `database` the datasource id (`chinook`),
`shortsha` the first seven characters of the engine commit and `config` the
config name (`retrieval` for a retrieval run). `-2`, `-3`... are added when the
name is taken. Every record holds:

| Field | What it is |
| --- | --- |
| `schema`, `kind` | The record format (2) and the benchmark |
| `recorded_at` | UTC date and time |
| `engine_version` | The installed `nl2sql-engine` version (the same for every run until a release) |
| `git_commit`, `git_dirty` | The engine's commit and whether its tracked files had uncommitted changes. Found by walking up from the installed `nl2sql` package to its `.git`, so a run from the demo folder of an editable install still names the repo commit; `"unknown"` for a wheel install or without git, never null |
| `note` | `--note`: what changed, e.g. `"slim prompts"` (null without one) |
| `dataset` | `chinook_gold.yaml`'s name and sha256, taken with LF line endings so a Windows checkout hashes the same |
| `database` | `datasource_id`, `engine` (`sqlite`), `schema_fingerprint` (a hash of the latest indexed schema snapshot's tables, columns, types and keys, so re-indexing an unchanged database keeps it) and the `tables` and `columns` counts. Built from the datasources the gold dataset's questions name, not from every datasource the project registered: the demo registers three and this set asks about Chinook, so a run there is still `chinook` with Chinook's fingerprint, and stays in the series recorded before the other two existed. Several datasources (a dataset that spans them) are joined with `+` |
| `config` | The config name, and for tier 2 `provider:model` per node |
| `metrics` | Tier 2: accuracy (strict) and lenient_accuracy with their 95% intervals, `pass_k`, answerability precision and recall, dollars total and per question, input / cached / output tokens per question, p50 and p95 latency, determinism, answer faithfulness. Retrieval: the report's `summary` |
| tier 2 only | `roles`, `passes`, `stopped`, `partial` and the config's full `scoreboard` |
| retrieval only | `settings` and the full `report` |

Records are never edited after writing: a run writes a new file, and
`publish` and `publish --from` refuse to overwrite one. The first retrieval
baseline was written before this layout and was moved into it with its
database, the commit that added it and the note "first retrieval baseline"
filled in (listed in its `backfilled` field).

#### Comparable runs

Two runs are comparable when they share the benchmark kind, the dataset sha
and the schema fingerprint, and for tier 2 the roles. `docs/benchmarks.md`
groups the history by benchmark, then database, and lists each group's runs
newest first. "Δ vs previous" compares a run with the previous run of the same
config on the same database: the change in strict accuracy, lenient accuracy
and faithfulness (in percentage points) and dollars per question for tier 2,
in table and column recall for retrieval. For tier 2 it then says how many
questions flipped each way and McNemar's exact p-value on them, read from the
two records' committed per-question results -- `3 flipped to fail, 1 to pass
(McNemar p=0.625)`. When the dataset, the schema or the roles changed, the
run starts a new series and the cell says so, e.g. `new series (schema
changed)`; a config's first run says `first run`. The README block has two
headed tables, tier 2 accuracy (latest run per config and database) and then
retrieval recall (latest run per database), with the headline deltas only; a
benchmark with no recorded run says so under its heading. Accuracy in the
history page carries its 95% interval, and the Passes column carries pass^k
when a run made more than one pass.

#### Publishing

`nl2sql benchmark publish` (no key, no network), run from the repo root,
reads every record in `benchmarks/<kind>/<database>/` and rewrites
`docs/benchmarks.md` and the block between `<!-- BENCHMARKS:START -->` and
`<!-- BENCHMARKS:END -->` in `README.md`. It reads no clock, so the same
records always give byte-identical pages. `--from DIR` (repeatable) first
copies the records of another project folder (its `benchmarks/`) that the
repo lacks: one already here with the same content is skipped, one with the
same name and other content is reported as a conflict, left as it is, and
makes the command exit 1 after publishing. Records in the old layout are
skipped with a warning. The workflow:

1. From the demo folder: `nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5`.
2. From the repo: `nl2sql benchmark publish --from <demo folder>`.
3. Commit the new records in `benchmarks/` with the `README.md` and
   `docs/benchmarks.md` changes.

`benchmarks/README.md` repeats the layout and workflow next to the records.
`packages/nl2sql/tests/unit/test_benchmark_records.py` checks in CI, with no
key, that the committed README block and `docs/benchmarks.md` are exactly what
`publish` makes from the committed records, and that every committed record
sits where its fields say.

### How tier 2 is tested

With no key and no spending: `packages/nl2sql/tests/e2e/test_benchmark_tier2_fake_llm.py`
points ordinary LLM config files at two `FakeLLMServer`s serving the gold
plans with canned usage (cached and reasoning tokens included), one with a
plan deliberately wrong. It checks the scoring, the comparison and its
differences, the dollars against the price table, the cap stopping mid-run
with a partial scoreboard, an unpriced model failing before any call, and two
passes with the plan cache off. A `--model gpt-5.4` config writes no
`base_url`, so its test points the OpenAI client at the fake server with the
client's own `OPENAI_BASE_URL` variable, and checks the database identity
the board records. Its fake synthesizer writes each answer from
the gold rows, so the good server's answers are faithful and the bad server's
answer on its wrong rows is not. `tests/unit/test_answer_faithfulness.py`
covers the faithfulness rules with good and bad answers. `tests/unit/test_tier2_scoreboard.py` and
`tests/cli/test_benchmark_tier2_command.py` cover the scoreboard, the lenient
score, the Wilson interval on known counts, McNemar's exact test on a
constructed table, pass^k, the gate firing and not firing, the deprecated
`--max-accuracy-drop`, the refusal to start without `--max-cost`, the exit codes,
`--model` and `--llm` resolution, the plan, the project-relative outputs,
`benchmark presets` and `publish --from`. `tests/unit/test_benchmark_presets.py`
covers the per-node `--model` config and preset lookup, and
`tests/unit/test_benchmark_records.py` the records, the git commit lookup,
comparability, the Δ and series breaks.

## Retrieval recall

Before the planner sees a schema, the schema retriever picks the tables and
columns it is given. A column it never sends is one the planner cannot use.
`nl2sql benchmark retrieval` measures that, per answerable gold question,
against `needed_tables` and `needed_columns`, with no key, no LLM and no cost:

```bash
nl2sql --env demo benchmark retrieval                       # writes <project>/benchmark_retrieval.json
nl2sql --env demo benchmark retrieval --record --note "k 10"  # also writes a record to <project>/benchmarks/retrieval/<database>/
nl2sql --env demo benchmark retrieval --baseline old.json   # the change against an earlier report or record
nl2sql --env demo benchmark retrieval --questions join,chinook_014
```

How it runs (`nl2sql/evaluation/retrieval_recall.py`):

- Chinook's 11 tables are under `SCHEMA_RETRIEVAL_FULL_SNAPSHOT_MAX_TABLES`
  (15), which would send the whole schema without a search, so the limit is
  set to 0 for the run and the vector search always runs.
- Each question is scored against the datasource its gold entry names, which
  for this set is always `chinook`, whatever else the project has registered.
  Which datasource the resolver's own vector search would have ranked first
  is reported separately as `datasource_top1_accuracy`; its answerability
  check is an LLM call and is skipped.
- The schema retriever runs as the pipeline runs it (MMR, k 8 table entries,
  then k 12 column and relationship entries of those tables), on the index
  and embedder the project has: the demo's is the local ONNX
  all-MiniLM-L6-v2. Its query is the question alone, since the decomposer
  that would add filters, groupings and expected columns is an LLM.
- What was sent is read from the retriever's retrieval record
  (`indexing/retrieval_trace.py`), the one a run trace keeps: the tables and
  columns the planner is given. A picked table with no picked column is sent
  with all its columns, as in the pipeline.

Per question the report has `table_recall` (needed tables sent / needed
tables), `column_recall` (needed `Table.Column`s sent / needed), the
`missed_tables` and `missed_columns`, `tables_sent` and `columns_sent`, and
`sent` (every table and column sent), plus the `datasource_id` it was scored
against and the `retrieved_datasource_id` the vector search ranked first.
`summary` holds the means over questions, how many had every needed table
(`perfect_tables`) or column (`perfect_columns`), the mean tables and columns
sent, and `datasource_top1_accuracy`, the share of questions whose top
candidate was the datasource the gold entry names. With one datasource
registered there is nothing to choose and it is 1.0; the demo registers
three, so it measures the router. `settings` holds
the k, the MMR settings and the embedder, so two reports are only compared
like for like. The command prints a row per question and the summary, and
always exits 0 when it ran: it reports, it does not gate.

### Reading the numbers

Read recall with the amount sent: sending all 11 tables would score 100%, so
a change that raises recall by sending more is not an improvement by itself.
A missed column is one the planner cannot select, filter or join on. Compare
runs with `--baseline`, which prints the change in each mean and every
question whose recall moved, or with the committed records: each `--record`
run is a JSON file in `benchmarks/retrieval/<database>/`, and `nl2sql
benchmark publish` lists them newest first in `docs/benchmarks.md`, with the
change in each recall against the previous comparable run.

The committed baseline (2026-09-22, engine 0.1.2, 39 questions):

| Table recall | Column recall | Every table | Every column | Sent on average |
| --- | --- | --- | --- | --- |
| 98.5% | 72.6% | 37 of 39 | 15 of 39 | 8.3 tables, 24.2 columns |

Most misses are columns of a table that was sent. Once one column entry of
a table is picked, or a relationship entry adds its key columns, only those
columns are sent rather than all of them, so `Invoice` often reaches the
planner as `BillingCountry` or a key without `Total` or `InvoiceDate`
(`chinook_014`, "How much did we earn in each year?", gets `Invoice` with
`BillingCountry` only). The two table misses
are `chinook_009` (`Customer`, `Invoice`) and `chinook_034` (`Artist`).

`packages/nl2sql/tests/e2e/test_benchmark_retrieval.py` runs the command on
the generated demo in the key-free integration job and checks that it covers
every answerable question and writes its report and record, not what the
recall is. `tests/unit/test_retrieval_recall.py` covers the scoring, the
means, the comparison and the records.
