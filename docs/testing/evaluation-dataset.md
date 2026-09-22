# Chinook Gold Evaluation Dataset

A set of about forty questions about the vendored Chinook database
(`nl2sql/cli/demo/data/chinook.sqlite`), each with hand-written SQL and the rows
that SQL returns. It is the reference an evaluation compares the engine's
answers against.

| File | What it holds |
| --- | --- |
| `packages/nl2sql/src/nl2sql/evaluation/datasets/chinook_gold.yaml` | The dataset |
| `packages/nl2sql/src/nl2sql/evaluation/gold.py` | `GoldQuestion` model, `load_gold_dataset()`, `execute_gold_sql()`, `regenerate()` |
| `packages/nl2sql/src/nl2sql/evaluation/datasets/chinook_gold_plans.yaml` | A hand-written `PlanModel` per answerable question, for [tier 1](#tier-1-gold-plans-through-the-code-nodes) |
| `packages/nl2sql/src/nl2sql/evaluation/tier1.py` | `load_gold_plans()`, the gold-plan fake LLM, `run_tier1()` |
| `packages/nl2sql/src/nl2sql/evaluation/evaluator.py` | Row comparison (`compare_results`) and per-role scoring (`score_case`) |
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
| `needed_tables` | Exactly the tables `gold_sql` reads, for retrieval recall |
| `needed_columns` | `Table.Column` names the answer needs, for retrieval recall |
| `expected` | Per demo role (`admin`, `analyst`, `viewer`): `allowed`, `refused` or `unanswerable` |
| `order_matters` | Whether a comparison should respect row order |
| `gold_sql` | Hand-written SQLite SQL, or `null` for an unanswerable question |
| `gold_result` | Rows `gold_sql` returns, as a list of `{column: value}` maps. Generated, never typed. `null` when `gold_sql` is `null` |

`expected` follows the demo policy in `nl2sql/cli/demo/chinook.py`
(`CHINOOK_POLICIES`): a role gets `refused` when any of `needed_tables` is
outside its `allowed_tables`, and `allowed` otherwise. Questions the database
cannot answer are `unanswerable` for every role. The `rbac-denial` tag marks
exactly the questions some role is refused.

Where `ORDER BY` has ties, `gold_sql` breaks them on a key (`CustomerId`,
`Country` and so on) so the result is deterministic, and the question carries
the `ties` tag. When the tie is only about order, `order_matters` is `false`.

## Regenerating the results

`gold_result` is always produced by running `gold_sql` against the database.
After adding or editing a question, set its `gold_result` to `null` (or leave
it stale) and run:

```bash
python -m nl2sql.evaluation.gold
```

It re-executes every `gold_sql`, rewrites the file in its canonical layout and
prints how many results it wrote. Commit the rewritten file.

## What the tests check

`pytest packages/nl2sql/tests/unit/test_chinook_gold_dataset.py` runs without
an API key and checks that:

- every entry validates against `GoldQuestion` and ids are unique;
- every `gold_sql` reproduces its committed `gold_result`, and the committed
  file is byte-for-byte what the generator writes;
- paraphrase groups share one result;
- every needed table and column exists, and `needed_tables` equals the tables
  in `gold_sql`;
- every role in `expected` exists in the demo policy and each outcome follows
  from it;
- the twelve demo questions (`CHINOOK_QUESTIONS`) are included verbatim.

## Running the benchmark

```bash
nl2sql --env demo benchmark --tier 1        # gold plans, no API key
nl2sql --env demo benchmark                 # full pipeline, the configured LLM
```

Either way every question runs once per role in its `expected` map (narrow
with `--include-ids` and `--role`), and each run is scored:

| Expected | Passes when |
| --- | --- |
| `allowed` | Status `success`, one result set, and its rows equal `gold_result` |
| `refused` | Status `error`, a `SECURITY_VIOLATION` whose message is the generic refusal (it names no table), and no rows |
| `unanswerable` | Status `error`, a `QUESTION_NOT_ANSWERABLE` from the datasource resolver's answerability check, and no rows |

Rows are compared by value, in selected column order: column names and
aliases are ignored, so `SELECT Country AS c` matches a gold `Country`
column but a swapped column order does not. Numbers match within 0.005,
because gold values are rounded to two decimals. Strings must be equal, so
`'2009'` is not `2009`. Row order counts only when `order_matters` is true.

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
has no subqueries, window functions or DISTINCT aggregates, so a few plans
express the answer differently from `gold_sql`: anti-joins for "longest
track per genre" (`chinook_008`) and "never purchased" (`chinook_039`),
conditional sums in HAVING for "jazz but never rock" (`chinook_009`), and
`COUNT(DISTINCT(x))` written as `COUNT` over a function named `DISTINCT`
(`chinook_011`, `chinook_034`). The header of the plans file lists these.

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
