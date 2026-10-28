# Schema & Metadata Layer

The schema layer provides **authoritative, versioned snapshots** and **structured retrieval** to ground planning. It is split into:

- **Schema contracts**: structural schema (tables, columns, foreign keys).
- **Schema metadata**: the database's own comments and statistics.
- **The state store**: immutable, versioned snapshots, and the diffs between them, kept by `storage.state`.
- **Schema retrieval**: staged lookup via schema chunks.

## Schema contracts and metadata

Schema contracts are defined in `nl2sql_sdk.schema`:

- `SchemaContract`: datasource_id, engine_type, tables
- `TableContract`: table ref, column contracts, foreign keys
- `ColumnContract`: name, type, nullability, primary key flag
- `ForeignKeyContract`: constrained/referred columns, cardinality, business meaning

Metadata complements contracts:

- `SchemaMetadata`: datasource description/domains, table metadata
- `TableMetadata`: table description, row_count, column metadata
- `ColumnMetadata`: description, stats, synonyms, PII flag
- `ColumnStatistics`: distinct counts, min/max, sample values (for a text
  column: every distinct value when there are at most 50, else the five most
  frequent; most frequent first, ties by value). A column the adapter could
  not profile has no statistics and an index issue; values are kept only
  with the datasource's `index.sample_values`. See
  [Reading a schema](../adapters/sqlalchemy.md#reading-a-schema)

## The state store

Snapshots are kept by the engine's **state store**, the `storage.state` slot
(see [Storage](../configuration/storage.md)). The built-in one is `sqlite`, a
file at `<storage.path>/state.db`; any store implementing
`nl2sql_sdk.stores.StateStore` can take its place. The same store holds the
plan cache, the playground's answer ratings, the review queue, conversations,
workspaces, dashboards with their last chart results, API keys and the index
build lock. The interface is version 4.

### Snapshots are immutable

`put_snapshot(snapshot)` stores a snapshot as a new row and returns its
`schema_version`, `YYYYMMDDhhmmss_<hash8>` (the time it was stored, and a hash
of its contract and metadata). A stored snapshot is never changed: in the
`sqlite` store a trigger refuses any `UPDATE` of the `schema_snapshots` table.

- Storing a snapshot whose contract and metadata are identical to a stored one
  returns that version, and writes nothing.
- A snapshot with the same structure but new metadata (statistics,
  descriptions) is a **new version**. A run that pinned the old
  version keeps reading exactly the snapshot its index build was made from.

The structural fingerprint (`nl2sql.schema.generate_schema_fingerprint`:
datasource ID, engine type, sorted tables, columns and foreign keys) is still
stored beside each row, and eval reports and records use it to identify a database.

### Schema diffs

When a build stores a snapshot whose structure differs from the version it
replaces, the orchestrator stores the diff between them beside the new
version (`put_schema_diff(diff)`, keyed by datasource and `diff.to_version`;
`get_schema_diff(datasource_id, schema_version)` reads it back) and publishes
`schema.drifted`. A version whose statistics alone changed gets no diff. The
diff (`nl2sql_sdk.schema.SchemaDiff`) and how to read it are in
[Schema Drift](drift.md). These two methods are why the store interface is
version 2.

### Which snapshots are kept

After each build of a datasource, indexing calls
`keep_snapshots(datasource_id, keep)`, which deletes every other snapshot of
the datasource, the plans cached on them and their diffs. `keep` is:

- every version a build pointer names, in **every** index namespace, so
  switching back to an earlier embedding model still finds its snapshot;
- the version the build just replaced, which runs in flight may have pinned;
- the newest `settings.schema.max_versions` (default 3).

Nothing else deletes a snapshot; there is no age limit.

### The plan cache and feedback

The **plan cache** holds validated, executed plans keyed by `(normalised
sub-query intent, run date, planner model and knowledge digest, datasource_id,
schema_version)`
(`get_plan`, `put_plan`). Deleting a snapshot deletes its cached plans;
`nl2sql cache clear` (`engine.clear_plan_cache()`) deletes them all and keeps
the snapshots. See [Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

The playground's **answer ratings** are kept too (`add_feedback`,
`list_feedback`, `clear_feedback`): one row per rated run, questions and SQL,
never rows. `nl2sql feedback clear` empties them and nothing else. See
[Feedback and Signals](../observability/feedback.md).

### Migrations

The `sqlite` store records each migration it has applied in a `nl2sql_meta`
table, and the engine applies the pending ones when it starts
(`StateStore.migrate()`). Migration 1 creates 0.5's three tables
(`schema_snapshots`, `plan_cache`, `feedback`) if they are missing, so a 0.5
`state.db` is adopted as it is; migration 2 adds the feedback columns that
came later; migration 3 adds the immutability trigger and the `locks` table;
migration 4 adds the `schema_diffs` table; migration 5 adds the `proposals`
table, the review queue (release 0.8, [Wave B](../architecture/wave_b.md#5-the-review-queue));
migration 6 adds Wave C's five tables, below. A 0.8 `state.db` is migrated in
place and keeps its rows.

### Conversations, dashboards and API keys

Release 0.9 ([Wave C](../architecture/wave_c.md#10-the-registry-and-the-state-store-after-wave-c)).
`StateStore` version 4 adds these methods. Each takes and returns a plain JSON
document (`dict`), because the SDK holds none of the engine's models; the
document carries the fields the store filters on. The engine methods that use
them arrive in C1 and D1 to D4.

| Table (migration 6) | Methods | Filtered on | Notes |
| --- | --- | --- | --- |
| `conversations` | `put_conversation`, `get_conversation`, `list_conversations(tenant_id, user_id, limit)`, `delete_conversation`, `purge_conversations(older_than)` | owner, `updated_at` | One document per conversation, rewritten each turn, read whole. Stored without result rows. `None` is an owner of its own in `list_*`, not a wildcard. |
| `workspaces` | `put_workspace`, `get_workspace`, `list_workspaces(tenant_id, user_id)`, `delete_workspace` | owner, `created_at` | Listed oldest first. A put on an existing id keeps its `created_at`, so renaming a workspace does not move it. |
| `dashboards` | `put_dashboard`, `get_dashboard`, `list_dashboards(tenant_id, user_id)`, `delete_dashboard`, `purge_dashboards(drafts_older_than)`, `list_scheduled_dashboards()` | owner, `status`, `updated_at`, whether it has a `schedule` | `purge_dashboards` deletes drafts only; saved dashboards are never purged. `list_scheduled_dashboards` serves the scheduler across owners. |
| `tile_runs` | `put_tile_runs(dashboard_id, docs)`, `get_tile_runs(dashboard_id)`, `delete_tile_runs(dashboard_id, tile_ids)` | dashboard, chart (`tile_id`), `at` | The last result of each chart, rows included. `put_tile_runs` replaces only the charts it lists and `delete_tile_runs` deletes only those it lists (an edit that changes what a chart shows discards its result). Deleted with the dashboard. |
| `api_keys` | `add_api_key`, `revoke_api_key(key_id, revoked_at)`, `get_api_key`, `list_api_keys` | `id`, `hash`, `revoked_at` | A hash, never a secret. `add_api_key` is a plain insert and raises `ValueError` when the `id` exists, so a clash never replaces another holder's key. `revoke_api_key` sets `revoked_at` and nothing else; it returns False for an unknown id. |

Stored chart results hold rows, so they sit in the owner's private state store
and open only after the access check.

Each document's timestamps are ISO 8601; a stamp with no zone is read as UTC.

### The lock

`lock(name, ttl_s)` is a lease held for a `with` block: in the `sqlite` store,
a `locks` row taken in a `BEGIN IMMEDIATE` transaction, with an expiry. Every
`engine.index.build()` holds the `index` lock, so `nl2sql index` is safe to run
while `nl2sql-api` serves the same `storage.path`: a second build started
meanwhile fails at once and says until when the lease runs. The lease expires
after an hour, so a build that died cannot block forever.

The `sqlite` store opens its file on first use, never when it is built, and
is safe to share between threads: each use of its one connection holds a lock.

## Which version is live

There is no "latest snapshot" to read. Indexing stores a datasource's
snapshot first and its index entries second; the index store's
`BuildPointer` for the datasource then records the `schema_version` the live
build was made from, and switching that pointer is the commit. The pointer
also records the `curated_hash` and the `knowledge_hash` the build was made
with, so health can tell that `curated.yaml` or the glossary has changed
since. A snapshot holds only what the database said: model-drafted
descriptions are proposals, and a person's words live in the glossary
(see [Indexing](../architecture/indexing.md#knowledge-in-the-index)). Every reader
(the resolver, the schema retriever, the curated matcher,
`engine.datasources.schema()`, `engine.curated.validate()`) resolves the
version through the pointer and reads `get_snapshot(datasource_id,
schema_version)`. A run reads the pointers once, when it starts. A snapshot
stored by a build that then failed is named by no pointer, so nothing reads
it. See [Indexing → The build pointer is the commit](../architecture/indexing.md#the-build-pointer-is-the-commit).

## Retrieval and authority

Schema retrieval resolves **authoritative** tables/columns from the state store, not from the index. Index entries are only used to identify candidates; final schema is resolved via the snapshot the live pointer names. If retrieval yields no candidates, the retriever falls back to the full snapshot. The entries and the snapshot come from the same build, so their versions cannot disagree.

See `../architecture/indexing.md` for retrieval stages, chunk types, and vector store behavior.

## Source references

- Contracts and metadata: `packages/sdk/src/nl2sql_sdk/schema.py`
- Fingerprinting: `packages/nl2sql/src/nl2sql/schema/fingerprint.py`
- Diffs and drift: `packages/nl2sql/src/nl2sql/schema/diff.py`, `drift.py`, `impact.py`
- The `StateStore` interface: `packages/sdk/src/nl2sql_sdk/stores.py`
- The `sqlite` state store: `packages/nl2sql/src/nl2sql/storage/sqlite.py`
- Which snapshots are kept: `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- The index lock: `packages/nl2sql/src/nl2sql/indexing/rebuild.py`
- Schema retriever: `packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
