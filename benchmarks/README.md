# Benchmark records

One JSON file per recorded benchmark run. `nl2sql benchmark publish` builds
[docs/benchmarks.md](../docs/benchmarks.md) and the README's results block from
them. Full detail: [the evaluation dataset](../docs/testing/evaluation-dataset.md#results-over-time).

## Layout

```
benchmarks/<kind>/<database>/<YYYY-MM-DD>_<shortsha>_<config>.json
```

- `kind`: `tier2` (the real model, one file per config) or `retrieval` (schema retrieval recall).
- `database`: the datasource id, e.g. `chinook`.
- `shortsha`: the engine commit the run used; `unknown` for a wheel install.
- `config`: the config name (`gpt-5.4`, `gpt-5.4-mini-helpers`, ...), or `retrieval`.
- `-2`, `-3`... when the name is taken.

## Fields

Every record has `schema` (2), `kind`, `recorded_at` (UTC), `engine_version`,
`git_commit` and `git_dirty` (of the installed engine's checkout, `"unknown"`
when there is none), `note` (`--note`), `dataset` (gold file name and sha256),
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

```bash
# from the demo folder
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5 --note "what changed"
nl2sql --env demo benchmark retrieval --record --note "what changed"

# from the repo root
nl2sql benchmark publish --from <demo folder>
git add benchmarks README.md docs/benchmarks.md
```
