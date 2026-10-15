# Evals

`engine.evals` measures how well the engine answers your questions. You
write **cases** (a question, what each role should get, and the SQL a person
reviewed) in **suite** files. A run asks every case once per role and scores it
with registered **scorers**, and the result is an `EvalReport`.

The package ships one suite, Chinook's 47 gold cases, and
`nl2sql init --demo` copies it next to the demo's `nl2sql.yaml`. The format
is in [Cases and suites](cases.md), and what makes a case pass is in
[Scorers](scorers.md). To build a suite for your own database, seed draft
cases from its schema and review them ([Benchmark your own database](seeding.md)).

## Configuring it

`evals.yaml` is a config section, like `curated.yaml`. Its keys are strict and
it is loaded with the config:

```yaml
# nl2sql.yaml
evals: { file: evals.yaml }

# evals.yaml
suites: [evals/chinook.yaml]       # relative to nl2sql.yaml
scorers: [result_match, outcome, faithfulness, retrieval_recall, routing]   # the default: all five
curated: true                      # mode full: curated answers on (the default)
max_cost_usd: 5.00                 # mode full's dollar cap over a whole run; it needs one
gate: { max_regressions: 2, alpha: 0.05, max_cost_increase: 0.20 }
```

A suite file is data. It is read when a run asks for it, never when the
engine starts, so a large suite does not slow down engine start. `gate`
configures the regression gate, which compares a run with a baseline.

## The facade

```python
from nl2sql import NL2SQL, load_config

engine = NL2SQL(load_config("nl2sql.yaml"))
report = engine.evals.run(mode="plans")          # no key, no cost
print(report.accuracy, report.accuracy_generated)
```

| Method | What it does |
| --- | --- |
| `engine.evals.load(path=None)` | The suites `evals.yaml` names, or the one file at `path`, as `EvalSuite`s |
| `engine.evals.run(suites=None, *, mode="full", llm=None, cases=None, roles=None, scorers=None, curated=None, passes=1, max_cost_usd=None, on_case=None, clarify=False)` | Runs every approved case once per role in its `outcome` map, `passes` times, and returns an `EvalReport` |
| `engine.evals.gate(report, baseline)` | The regression gate: `report` against a baseline report, record or `benchmarks/` folder, with `evals.yaml`'s `gate` settings. Returns a `GateResult` ([The gate](#the-regression-gate)) |
| `engine.evals.regenerate(path, *, allow_packaged=False)` | Runs each approved case's `sql` and `alt_sql` through its datasource's adapter and writes `rows` and `alt_rows` ([Regenerating rows](cases.md#regenerating-rows)). A suite the package ships is refused unless `allow_packaged` |
| `engine.evals.seed(datasource_id, *, count=20, out, user_context, max_cost_usd=None)` | Drafts up to `count` cases from the datasource's schema, as `user_context` sees it, and appends them to `out` as `status: draft`. Returns a `SeedReport` ([Benchmark your own database](seeding.md)) |
| `engine.evals.scorers()` | The registered scorer names: the five built-ins and any plugin's |

The `run` arguments:

- `cases` narrows the run by case id or tag. A selector that matches nothing is
  an error.
- `roles` narrows it by role.
- An argument left as `None` takes its value from `evals.yaml`.

`on_case(config, record, spent)` is called after each case. It gets the
config's name, the case's record and the dollars spent so far.

## The modes

| Mode | What runs | Model calls | Curated answers | Plan cache |
| --- | --- | --- | --- | --- |
| `full` | The whole pipeline, on the configured LLM or on each named config | yes; needs `max_cost_usd` | as `curated` says (on by default) | **off** |
| `plans` | Each case's hand-written `plan` through the validator, generator and executor, on the gold-plan fake LLM | none | **off** | as configured |
| `retrieval` | Datasource routing and schema retrieval only | none (the configured embedder) | **off** | not used |

Every mode runs on a runtime derived from the engine's (`ctx.derive`), so the
engine serving traffic never changes. In each mode:

- **Refusals are generic.** A refusal message never names a table
  (`policies.refusal_names_tables` is off for the run). The `outcome` scorer
  checks for the generic message.
- **Mode `full` turns the plan cache off.** A second pass asks the planner
  again instead of replaying the first pass's plans.
- **Modes `plans` and `retrieval` turn curated answers off.** The curated
  matcher makes a model call the gold-plan fake cannot answer, and a verified
  hit would skip the plan or the retrieval being measured.
- **Mode `retrieval` always runs the vector search.** Its runtime sets
  `settings.retrieval.full_snapshot_max_tables` to 0, because Chinook's 11
  tables are under the limit that would otherwise skip the search.

### Mode full: the configured model, priced and capped

`llm` takes one of three values:

- `None`: the engine's own `llm` section, reported as `default`.
- One `LLM`.
- A mapping of names to `LLM` sections. Each one runs on its own derived
  runtime over the same cases, and the report compares them.

The named configs in `nl2sql/evaluation/presets/` are `llm` sections too:
`load_llm_file(path)` and `model_config("gpt-5.4")` in
`nl2sql.evaluation.presets` build one.

Every call costs money, so mode full checks prices before it starts and
checks the cap before every case:

- **Every model must have a price.** Before any call, every model that any
  node of any config would use must have a price, in `llm.prices` or the
  engine's dated table ([Prices](../configuration/llm.md#prices)). A missing
  price is an `UnknownPriceError` naming the config, the node and the model.
- **The run stops before it could pass the cap.** Before each case, the run
  adds the most expensive case so far to its running total. If that could
  pass `max_cost_usd`, the run stops and the report says
  `stopped: "max_cost"`.
- **The first case always runs.** So a run can pass the cap by at most one
  case's cost.
- **An unknown cost is not counted as zero.** A call that reported no token
  counts (a failed call) cannot be priced. The run goes on, leaves it out of
  `spent` (so `spent` is a lower bound), counts it in the report's
  `unknown_cost_calls`, and logs one warning.

Each call is priced by the model its node is configured with, and the dollars
are in `report.spent` and each record's `cost`.

### Curated answers in mode full

`curated: true` is the default. Each case record says where its answer came
from in `source` (`verified` or `generated`).

A case that a verified query answered is run a **second time with curated
answers off**, and that second run is scored as the case's generated answer
(in the record's `generated`). Only the cases a verified query answered pay
for the second run.

The report gives two accuracies:

| Accuracy | What it measures |
| --- | --- |
| `accuracy` | What users see |
| `accuracy_generated` | Every case on the generated path: the model's own skill, comparable across configs |

Both are also split by source.

### Clarification

A case may expect the engine to ask the caller to narrow the question
(outcome `clarify`). Wave B adds that result. Until then, evals run with
`clarify=False`, the default: those role runs are left out and counted in
`report.skipped`. With `clarify=True` they run, and the `outcome` scorer
passes them when the run's status is `needs_clarification`.

## The report

`EvalReport` has:

- `mode` and `suites` (each suite's name, path and SHA-256 digest).
- `roles`, `passes`, `curated`, `clarify`, `max_cost_usd`, `spent`, `stopped`,
  `skipped` and `settings` (mode retrieval's retrieval settings).
- `database`: the datasource, engine and schema fingerprint of the
  datasources the suites name.
- `configs`: one **board** per LLM config. Mode `plans` reports one board
  named `plans`, and mode `retrieval` one named `retrieval`.
- `comparison`, when there are several configs.

`report.accuracy` and `report.accuracy_generated` read the first config's
board.

**Two accuracies, on purpose.** `accuracy.overall` counts a case as passed
only when every scorer that applies passes. In mode full with the default
scorers, that includes `faithfulness`, `retrieval_recall` at 1.0 and
`routing`. So it is stricter than the accuracy 0.6's tier 2 published.
`accuracy.execution` is that older measure alone: `result_match` and
`outcome` passed, no other scorer counted. Compare it, not `overall`, with
the runs published before 0.7 (`docs/benchmarks.md`).

A board holds:

| Field | What it is |
| --- | --- |
| `accuracy` | `overall` (what users see), `lenient`, `generated` and `execution`, each with a 95% Wilson interval; `pass_k` over several passes; and the rates `by_tag`, `by_difficulty`, `by_source` and `generated_by_source` |
| `summary` | `pass`, `fail`, `error` and `skip` counts per role and in total |
| `scores` | Per scorer: how many cases it applied to, passed, failed and errored on, and its mean value |
| `retrieval` | The mean table and column recall over the cases `retrieval_recall` scored, how many were perfect, and how much was sent |
| `answerability`, `faithfulness`, `tokens_by_node`, `cost`, `latency`, `retries`, `errors_by_code`, `determinism` | As for a single run, summed or averaged over the cases |
| `models`, `planned_cases`, `completed_cases` | The config's model per node, and the cases the run would make and made |
| `results` | One record per case run: its `status` and `reason`, every scorer's `score`, the SQL, the answer, the plans, `source` and `generated` |

Accuracy over 47 cases has an interval about 14 points wide, so two runs are
compared case by case, not headline to headline. The statistics are in
[the Chinook suite](../testing/evaluation-dataset.md#reading-a-run-intervals-flips-and-passk).

## Events

Each case runs under its own run scope (`QueryAPI.open_scope` and
`close_scope`), so it publishes `run.started` and `run.finished` like any
question. This includes mode retrieval, where no graph runs: its scope
closes with an empty result. Every finished case also publishes `eval.case` on the engine's bus,
with the suite, case, role, whether it passed, the answer's source, each
scorer's verdict and its cost. The end of the run publishes `eval.finished`,
with the suites, the number of cases, both accuracies and the dollars spent.

## The regression gate

`engine.evals.gate(report, baseline)` compares a run with a baseline and
returns a `GateResult`:

| Field | What it is |
| --- | --- |
| `ok` | True only when the runs were compared and nothing regressed |
| `regressions` | One line per problem: too many cases flipped to fail, a significant drop, or a cost rise |
| `series_break` | Why nothing was compared: another suite digest, another mode, or no config in common |
| `compared` | Per config and measure: the cases that flipped each way, McNemar's p-value, both accuracies and both costs per case |

The baseline is an `EvalReport` (or the JSON `nl2sql eval run` writes), a
record from `benchmarks/`, or a path to either. A path to a `benchmarks/`
folder means its newest record of the run's mode, database and config.

**Only the same suite and mode compare.** Two runs are compared only when the
suites' digests match and the mode is the same. Anything else is a series
break: `ok` is false and nothing is compared.

**The gate reads execution accuracy.** A case passes when `result_match` and
`outcome` pass; no other scorer counts. That is `accuracy.execution`, the
measure 0.6's tier 2 published, so a run compares with the history published
before 0.7 and a faithfulness verdict alone is never a regression. A
`kind: routing` case has no rows to check and is left out of it. With
curated answers on, it is checked twice: as users see it, and on the
generated path. Mode retrieval has no execution, so its cases are read by
their status.

For each config both runs have, the gate fails when:

- more cases passed in the baseline and fail now than are allowed. Mode
  full allows `max_regressions` (2 by default), because the model is noisy.
  Modes plans and retrieval run no model, so they allow **none**: any flip is
  a real break;
- in mode full, more cases were lost than gained and McNemar's exact
  **two-sided** p-value on the flips is under `alpha`; or
- cost per case rose by more than `max_cost_increase`.

Comparable means what the history page means by it (`records.series_key`):
the same mode, suite digest and schema fingerprint and, in mode full, the
same roles and curated answers. Both runs must also have run the same roles,
so a role left out cannot quietly shrink the comparison.

The statistics are in
[the Chinook suite](../testing/evaluation-dataset.md#reading-a-run-intervals-flips-and-passk).

## The CLI

| Command | What it does |
| --- | --- |
| `nl2sql eval run [--suite PATH ...] [--mode full\|plans\|retrieval] [--llm SPEC ...] [--cases ID/TAG ...] [--passes N] [--max-cost USD] [--baseline PATH] [--out FILE]` | `engine.evals.run`, then `engine.evals.gate` with `--baseline` |
| `nl2sql eval regenerate PATH` | `engine.evals.regenerate`; exits 1 when a case's SQL failed |
| `nl2sql eval seed --datasource ID --out PATH [--count N] [--role R] [--max-cost USD]` | `engine.evals.seed`, as `--role` (else `policies.default_role`); exits 2 on a refusal, 3 at the cost cap, and refuses to run when `CI` is set ([Benchmark your own database](seeding.md)) |
| `nl2sql eval publish [REPORT ...] [--note TEXT] [--from DIR ...]` | Records each report under `benchmarks/`, then rebuilds [the results page](../benchmarks.md) and the README block. Run from the repo root |

`--llm` names an LLM config to compare in mode full, and repeats:

- `gpt-5.4`, `mini-helpers` or `claude-planner`: a preset from
  `nl2sql/evaluation/presets/`, named by itself;
- `mine.yaml`: a file holding an `llm` section, named by its stem;
- `NAME=PRESET` or `NAME=FILE`: either, under a name you choose.

Without it, the config's own `llm` section runs as `default`.

`nl2sql eval run` writes the report to `--out`, by default
`eval_<mode>.json` next to `nl2sql.yaml`, with a `run` stamp: when it ran,
and the engine version and commit. `nl2sql eval publish` records that stamp,
not the time it publishes.

| Exit code | When |
| --- | --- |
| 0 | The run finished, and nothing regressed against `--baseline` |
| 1 | A regression against `--baseline` |
| 2 | A usage error: a bad `--llm`, a run the engine refuses (no cost cap, an unpriced model), mode full with `CI` set (whatever its value, `CI=false` included), a missing or unreadable baseline, or one that cannot be compared |
| 3 | The cost cap stopped mode full early: the report is partial |

```bash
# From a demo folder: the key-free gate CI runs, against the repo's records
nl2sql eval run --mode plans --baseline ../benchmarks
# A paid run comparing two configs, under a $10 cap
nl2sql eval run --mode full --llm gpt-5.4 --llm mini=mini-helpers --max-cost 10
# From the repo root: record it and rebuild the results page
nl2sql eval publish demo/eval_full.json --note "slim prompts"
```

## In CI

The key-free integration job runs the eval gate on the demo's Chinook suite
and on [the routing suite](cases.md#the-routing-suite), after indexing them:

```bash
nl2sql --config demo-ci/nl2sql.yaml eval run --mode plans --baseline benchmarks
nl2sql --config demo-ci/nl2sql.yaml eval run --mode retrieval --baseline benchmarks
nl2sql --config demo-ci/nl2sql.yaml eval run --mode retrieval --suite evals/routing.yaml \
  --baseline benchmarks --out demo-ci/eval_routing.json
```

Each run is gated against the newest record of its mode and database in
`benchmarks/` (`plans/chinook/`, `retrieval/chinook/` and
`retrieval/chinook-support-webanalytics/`). The three reports are uploaded
as the `eval-reports` artifact whether the gate passed or not. On the routing
suite, a case that loses its routing top-1 or a shortlisted datasource is a
flip, and mode retrieval allows none. A changed suite or schema starts a new
series, so the gate exits 2 until the baselines are recorded again. The same job also runs `tests/e2e/test_evals_plans.py`, where
every case must pass for every role.

### Re-recording CI's baselines

CI's plans and retrieval baselines are recorded **on CI's own runner**
(`ubuntu-latest`, Python 3.12), never on a laptop: retrieval recall passes a
case only at 100% column recall, which near-tie rankings of the local ONNX
embedder can move between platforms.

1. Push the change. The integration job's "Eval gate" steps run the three
   gates and upload `eval_plans.json`, `eval_retrieval.json` and
   `eval_routing.json` as the `eval-reports` artifact, pass or fail.
2. Download them: `gh run download <run id> -n eval-reports -D eval-reports`.
3. From the repo root, record the ones the change moved, naming where they
   came from:
   `nl2sql eval publish eval-reports/eval_retrieval.json eval-reports/eval_routing.json --note "CI baseline: ubuntu-latest, Python 3.12, https://github.com/nadeem4/nl2sql/actions/runs/<run id>"`.
4. Commit the records and the regenerated pages in the same PR, and push.
   The next run gates against them.

Never edit a record by hand, `engine_version` included. A change that moves
the gate on purpose (a suite change, routing or retrieval work) re-records
the baselines it moves this way, in the PR that makes the change.

Mode `full` spends a key: `nl2sql eval run --mode full` refuses to start when
`CI` is set. The owner runs it before a release
([Releasing](../development/releasing.md#the-pre-release-eval)).
