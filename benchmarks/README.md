# Benchmark records

One JSON file per recorded eval run. `nl2sql.evaluation.records.publish()` builds
[docs/benchmarks.md](../docs/benchmarks.md) and the README's results block from
them. Full detail: [the Chinook suite](../docs/testing/evaluation-dataset.md#results-over-time).

## Layout

```
benchmarks/<kind>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json
```

- `kind`: `tier2` (mode full: the real model, one file per config) or `retrieval` (schema retrieval recall).
- `database`: the datasource id the suite's cases name, e.g. `chinook` - not
  every datasource the project has registered, so the demo's `support` and
  `webanalytics` leave a Chinook run's file, identity and series alone.
- `shortsha`: the engine commit the run used; `unknown` for a wheel install.
- `config`: the config name (`gpt-5.4`, `gpt-5.4-mini-helpers`, ...), or `retrieval`.
- `-2`, `-3`... when the name is taken.

## Fields

Every record has `schema` (2), `kind`, `recorded_at` (UTC), `engine_version`,
`git_commit` and `git_dirty` (of the installed engine's checkout, `"unknown"`
when there is none), `note`, `dataset` (the suite file's name and sha256),
`database` (`datasource_id`, `engine`, `schema_fingerprint`, `tables`,
`columns`), `config` and `metrics`. Tier 2 records add `roles`, `passes`,
`stopped`, `partial` and the full `scoreboard`; retrieval records add
`settings` and the full `report`. A record moved in from an older layout
lists what was filled in afterwards in `backfilled`.

Two runs are comparable when they share the kind, the dataset sha, the schema
fingerprint and, for tier 2, the roles. A run that changes any of them starts
a new series in the history.

## Rules

Records are immutable: never edit or rename one. A new run writes a new file;
`publish --from` never overwrites a record here and reports a clash as a
conflict.

## Workflow

Run the eval with `engine.evals.run` (mode `full` or `retrieval`), write its
records with `nl2sql.evaluation.records`, then from the repo root:

```bash
python -c "from nl2sql.evaluation.records import publish; publish()"
git add benchmarks README.md docs/benchmarks.md
```
