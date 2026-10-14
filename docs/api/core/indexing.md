# Indexing API

## Purpose
`engine.index`: build the vector index that retrieval and grounding read,
report its health, clear it, and inspect one search of it.

## Responsibilities
- Build the index for some or every datasource, beside the live entries.
- Report each datasource's progress as it goes, for a console or a job.
- Report the index's health, clear it, and run one inspected search (and say
  what that search uses by default).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/indexing_api.py` (`IndexAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/types.py` (`IndexProgress`)
- `packages/nl2sql/src/nl2sql/indexing/rebuild.py`
- `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- `packages/nl2sql/src/nl2sql/indexing/vector_store.py`
- `packages/nl2sql/src/nl2sql/indexing/enrichment_service.py`
- `packages/nl2sql/src/nl2sql/schema/store.py`

## Public Surface

### index.build

Signature:
`build(datasource_ids=None, *, full=False, curated_only=False, enrich=False, on_progress=None, on_datasource=None, switch_guard=None) -> RebuildResult`

| name | meaning |
| --- | --- |
| `datasource_ids` | Only these datasources; every configured one when `None`. Ignored with `full`. |
| `full` | Rebuild every datasource into a new collection and swap it in when all succeed. Needed after changing the embedding model. |
| `curated_only` | Re-index only the verified queries, keeping each datasource's schema entries: no schema read, no model call. Not with `full`. |
| `enrich` | Ask the `indexing_enrichment` LLM for table and column descriptions. Off by default: it spends tokens. |
| `on_progress` | Called with a sentence before each step. |
| `on_datasource` | Called with an `IndexProgress` as each datasource starts, is done or fails. |
| `switch_guard` | A context manager factory held around each switch (the playground holds questions in flight back with it). |

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

Raises:
- `ValueError` with both `full` and `curated_only`.
- `EmbeddingModelMismatchError` without `full`, when the collection records a
  different embedding model (rebuild with `full=True`, `nl2sql index --full`).

A failure of one datasource never raises: it is in `errors`, and that
datasource keeps its previous entries. Enrichment errors are never raised:
enrichment is best-effort and degrades to the unenriched snapshot.

Each datasource's new entries are written under a new build id, switched in
when all are written, and the previous ones deleted. Other datasources'
entries are never touched.

### index.health

Signature:
`health() -> dict`

`status` (`ok`, `empty`, `stale`, `missing`), `total`, `counts` by entry
type, `built_at`, `embedding_model`, one entry per registered datasource
(`entries`, `index_version`, `snapshot_version`, `built_at`), `problems` and
`warnings`. `missing` when no vector store is configured. A datasource that
failed to start is a warning: "Datasource 'x' is unavailable: <reason>".
`check()` reports its `status`.

### index.clear

Signature:
`clear() -> None`

Deletes and reinitializes the vector store collection, for every datasource.
Every question fails at the resolver until the index is built again; to
refresh an index, build it instead, which never leaves it empty.

### index.inspect

Signature:
`inspect(query, k=8, lambda_mult=None, types=None, datasource_id=None) -> dict`

One MMR search of the live index, as the engine runs it: the candidate pool
with scores, the picks in order and what was dropped. `types` limits the
entry types (`schema.table`, ...). It reads index metadata only and needs no
LLM. Raises `LookupError` when no vector store is configured.

### index.inspect_defaults

Signature:
`inspect_defaults() -> dict`

What `inspect` searches with when not told otherwise, which is what the
pipeline's own retrieval uses: `k` (8 picks), `lambda_mult` (1 is relevance
only, 0 is difference only), `fetch_multiplier` (the candidate pool is `k`
times it) and `types`, every entry type the index holds
(`schema.datasource`, `schema.table`, `schema.column`, `schema.relationship`,
`schema.metric`). The playground's Retrieval inspector starts from it.

## Execution Lifecycle
- Fetch schema via adapter.
- Only with `enrich=True`: enrich schema metadata using the `indexing_enrichment`
  LLM. Enrichment fills blanks only: a table or column comment read from the
  database is never replaced. If the client cannot be built (no API key), the
  endpoint is unreachable, or the call fails, the unenriched snapshot is used
  and indexing continues.
- Register schema snapshot and version.
- Build schema chunks and refresh vector store.
