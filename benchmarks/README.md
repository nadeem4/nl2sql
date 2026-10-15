# Benchmark records

One JSON file per recorded eval run and config. `nl2sql eval publish` builds
[docs/benchmarks.md](../docs/benchmarks.md) and the README's results block from
them. Full detail: [the Chinook suite](../docs/testing/evaluation-dataset.md#results-over-time).

## Layout

```
benchmarks/<mode>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json
```

- `mode`: `full` (the real model, one file per config), `plans` (the gold plans on a fake LLM) or
  `retrieval` (schema retrieval recall). The records 0.6 wrote as `tier2` are mode full runs.
- `database`: the datasource id the suite's cases name, e.g. `chinook` - not
  every datasource the project has registered, so the demo's `support` and
  `webanalytics` leave a Chinook run's file, identity and series alone.
- `shortsha`: the engine commit the run used; `unknown` for a wheel install.
- `config`: the config name (`gpt-5.4`, `gpt-5.4-mini-helpers`, ...), or `plans` / `retrieval`.
- `-2`, `-3`... when the name is taken.

## Fields

Every record has `schema` (2), `kind`, `recorded_at` (UTC), `engine_version`,
`git_commit` and `git_dirty` (of the installed engine's checkout, `"unknown"`
when there is none), `note`, `dataset` (the suite file's name and sha256),
`database` (`datasource_id`, `engine`, `schema_fingerprint`, `tables`,
`columns`), `config` and `metrics`. `dataset` is the suite's name and digest;
`metrics.accuracy` is execution accuracy, the measure 0.6 published. Tier 2 records add `roles`, `passes`,
`stopped`, `partial` and the full `scoreboard`; retrieval records add
`settings` and the full `report`. A record moved in from an older layout
lists what was filled in afterwards in `backfilled`.

Two runs are comparable when they share the mode, the suite digest, the schema
fingerprint and, for mode full, the roles and curated answers. A run that changes any of them starts
a new series in the history.

## Rules

Records are immutable: never edit or rename one. A new run writes a new file;
`publish --from` never overwrites a record here and reports a clash as a
conflict.

A record is one compact JSON line, and its case results keep only each
case's verdicts and cost: enough for the gate and the pages. The full report
(SQL, plans, tokens, timings) is the `eval_<mode>.json` that `nl2sql eval run`
writes; CI keeps it as an artifact and it is never committed. The 0.6 records
are kept as they were written.

## Workflow

```bash
nl2sql eval run --mode full --llm gpt-5.4 --max-cost 5     # from a demo folder
nl2sql eval publish <demo folder>/eval_full.json --note "what changed"   # from the repo root
git add benchmarks README.md docs/benchmarks.md
```

## The recording rule: CI's baselines come from CI

CI's plans and retrieval baselines are recorded **on CI's own runner**
(`ubuntu-latest`, Python 3.12), never on a laptop: retrieval recall passes a
case only at 100% column recall, which near-tie rankings of the local ONNX
embedder can move between platforms. To record or re-record them:

1. Push the change. The integration job's "Eval gate" step runs both modes and
   uploads `eval_plans.json` and `eval_retrieval.json` as the `eval-reports`
   artifact, pass or fail.
2. Download them: `gh run download <run id> -n eval-reports -D eval-reports`.
3. From the repo root, record them, naming where they came from:
   `nl2sql eval publish eval-reports/eval_plans.json eval-reports/eval_retrieval.json --note "CI baseline: ubuntu-latest, Python 3.12, https://github.com/nadeem4/nl2sql/actions/runs/<run id>"`.
4. Commit the two records and the regenerated pages in the same PR, and push.
   The next run gates against them.

Never edit a record by hand, `engine_version` included. A change that moves
the gate on purpose (a suite change, routing or retrieval work) re-records
both baselines this way, in the PR that makes the change.
