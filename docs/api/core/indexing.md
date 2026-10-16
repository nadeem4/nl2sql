# Indexing API

## Purpose
`engine.index`: build the vector index that retrieval and grounding read,
report its health, clear it, prune other embedding models' indexes,
inspect one search of it, and measure schema drift.

## Responsibilities
- Build the index for some or every datasource, beside the live entries.
- Report each datasource's progress as it goes, for a console or a job.
- Report the index's health, clear it, and run one inspected search (and say
  what that search uses by default).
- Compare each live database with the schema its build was made from, and
  read the stored versions and the diffs between them, as the caller may see
  them.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/indexing_api.py` (`IndexAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/types.py` (`IndexProgress`)
- `packages/nl2sql/src/nl2sql/indexing/rebuild.py`
- `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- `packages/nl2sql/src/nl2sql/indexing/retrieval.py`
- `packages/nl2sql/src/nl2sql/indexing/health.py`
- `packages/nl2sql/src/nl2sql/storage/chroma.py`
- `packages/nl2sql/src/nl2sql/indexing/enrichment_service.py`
- `packages/nl2sql/src/nl2sql/storage/sqlite.py`
- `packages/nl2sql/src/nl2sql/schema/drift.py`, `diff.py`, `impact.py` (schema drift)

## Public Surface

### index.build

Signature:
`build(datasource_ids=None, *, curated_only=False, enrich=False, on_progress=None, on_datasource=None) -> RebuildResult`

| name | meaning |
| --- | --- |
| `datasource_ids` | Only these datasources; every configured one when `None`. |
| `curated_only` | Re-index only the verified queries, keeping each datasource's schema entries: no schema read, no model call. |
| `enrich` | Ask the `profiler` LLM for table and column descriptions. Off by default: it spends tokens. |
| `on_progress` | Called with a sentence before each step. |
| `on_datasource` | Called with an `IndexProgress` as each datasource starts, is done or fails. |

Returns a `RebuildResult`: `ok` (every requested datasource's new entries are
live), `stats` (one dict per indexed datasource: `datasource_id`,
`schema_version` and the entry counts), `empty` and `errors` (`datasource_id`,
`error`).

`IndexProgress` is a frozen dataclass, exported from `nl2sql`, that serves a
console line and a job's status record alike:

| field | meaning |
| --- | --- |
| `datasource_id` | The datasource. |
| `state` | `started`, then `done` or `failed`. A datasource that is unavailable or not configured is `failed` without `started`. |
| `counts` | With `done`: the entries written, by kind. |
| `reason` | With `failed`: why. |

```python
from nl2sql import IndexProgress

def show(progress: IndexProgress) -> None:
    print(progress.datasource_id, progress.state, progress.counts or progress.reason or "")

result = engine.index.build(["shop"], on_datasource=show)
```

A failure of one datasource never raises: it is in `errors`, and that
datasource keeps its previous build and schema version. Enrichment errors are
never raised: enrichment is best-effort and degrades to the unenriched
snapshot.

The build goes into the namespace of the configured embedding model. Each
datasource's new entries are written under a new build id, and its build
pointer (the build id and the schema version it was made from) is switched to
them when all are written: that switch is the commit. The build it replaces is
kept for runs in flight, which pinned the pointers when they started; older
builds are deleted. Other datasources' entries are never touched. A changed
embedding model needs no special build: its namespace starts empty.

### index.health

Signature:
`health() -> dict`

Health of the configured embedding model's index: `status` (`ok`,
`empty`, `stale`, `missing`), `total`, `counts` by entry type, `built_at`,
`embedding_model`, `namespace`, one entry per registered datasource
(`entries`, `index_version`, the schema version its live build was made
from, and `built_at`), `problems` and `warnings`. `missing` when the model has
no namespace yet (never indexed, or the model changed): run `nl2sql index`.
A datasource that failed to start is a warning: "Datasource 'x' is
unavailable: <reason>". `check()` reports its `status`.

### index.clear

Signature:
`clear() -> None`

Drops the configured embedding model's namespace, for every datasource.
Every question fails at the resolver until the index is built again; to
refresh an index, build it instead, which never leaves it empty.

### index.prune

Signature:
`prune() -> list[str]`

Drops the index namespace of every other embedding model, including 0.5's
`nl2sql_store` collection, which this version never reads, and returns their
names. The current model's namespace is kept. `nl2sql index prune` calls it.

### index.inspect

Signature:
`inspect(query, k=8, lambda_mult=None, types=None, datasource_id=None) -> dict`

One MMR search of the live index, as the engine runs it: the candidate pool
with scores, the picks in order and what was dropped. `types` limits the
entry types (`schema.table`, ...). It reads index metadata only and needs no
LLM.

### index.check

Signature:
`check(datasource_ids=None, *, user_context) -> list[DriftReport]`

Compares each datasource's live database (reflected with its `index` scope,
catalog only, no profiling) with the snapshot its live build was made from.
Each `DriftReport` has `datasource_id`, `status` (`unchanged`, `drifted`,
`not_indexed`, `failed`), `live_version`, `checked_at`, and for `drifted` the
`diff` (a `SchemaDiff`, `to_version` `None`) and its `impact` (a
`DriftImpact`: verified queries, policy rules and preferred tables it
breaks); `error` (redacted) for `failed`. Drift publishes `schema.drifted`,
once per drift and engine.
Nothing is stored and nothing is rebuilt. `user_context` is required: the
caller sees drift only in tables its scope may read, a datasource it cannot
reach is left out (or, named, is a `ValueError` like an unknown one). See
[Schema Drift](../../schema/drift.md).

### index.versions

Signature:
`versions(datasource_id, *, user_context) -> list[SchemaVersionInfo]`

The datasource's stored schema versions, newest first: `schema_version`,
`fingerprint` (of the schema without the tables the caller may not read), `live` (the live build pointer names it) and `has_diff` (a
build stored the diff that introduced it, with something the caller may
read).

### index.diff

Signature:
`diff(datasource_id, schema_version=None, *, user_context) -> SchemaDiff | None`

The stored diff that introduced `schema_version` (the live one by default),
as the caller may see it; `None` for a first build, a version whose
structure did not change, or drift only in tables the caller may not read.

### index.inspect_defaults

Signature:
`inspect_defaults() -> dict`

What `inspect` searches with when not told otherwise, which is what the
pipeline's own retrieval uses: `k` (8 picks), `lambda_mult` (1 is relevance
only, 0 is difference only), `fetch_multiplier` (the candidate pool is `k`
times it) and `types`, every entry type the index holds
(`schema.datasource`, `schema.datasource_example`, `schema.table`,
`schema.column`, `schema.relationship`, `schema.metric`). The playground's Retrieval inspector starts from it.

## Execution Lifecycle
- Fetch schema via adapter.
- Only with `enrich=True`: enrich schema metadata using the `profiler`
  LLM. Enrichment fills blanks only: a table or column comment read from the
  database is never replaced. If the client cannot be built (no API key), the
  endpoint is unreachable, or the call fails, the unenriched snapshot is used
  and indexing continues.
- Store the schema snapshot and its version (nothing reads it yet).
- Build the chunks, embed them, write them as a new build, and switch the
  datasource's build pointer to it.
