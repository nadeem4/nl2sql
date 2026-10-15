# Schema & Metadata Layer

The schema layer provides **authoritative, versioned snapshots** and **structured retrieval** to ground planning. It is split into:

- **Schema contracts**: structural schema (tables, columns, foreign keys).
- **Schema metadata**: descriptive enrichment and statistics.
- **The state store**: immutable, versioned snapshots, kept by `storage.state`.
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
plan cache, the playground's answer ratings and the index build lock.

### Snapshots are immutable

`put_snapshot(snapshot)` stores a snapshot as a new row and returns its
`schema_version`, `YYYYMMDDhhmmss_<hash8>` (the time it was stored, and a hash
of its contract and metadata). A stored snapshot is never changed: in the
`sqlite` store a trigger refuses any `UPDATE` of the `schema_snapshots` table.

- Storing a snapshot whose contract and metadata are identical to a stored one
  returns that version, and writes nothing.
- A snapshot with the same structure but new metadata (statistics,
  descriptions, enrichment) is a **new version**. A run that pinned the old
  version keeps reading exactly the snapshot its index build was made from.

The structural fingerprint (`nl2sql.schema.generate_schema_fingerprint`:
datasource ID, engine type, sorted tables, columns and foreign keys) is still
stored beside each row, and eval reports and records use it to identify a database.

### Which snapshots are kept

After each build of a datasource, indexing calls
`keep_snapshots(datasource_id, keep)`, which deletes every other snapshot of
the datasource and the plans cached on them. `keep` is:

- every version a build pointer names, in **every** index namespace, so
  switching back to an earlier embedding model still finds its snapshot;
- the version the build just replaced, which runs in flight may have pinned;
- the newest `settings.schema.max_versions` (default 3).

Nothing else deletes a snapshot; there is no age limit.

### The plan cache and feedback

The **plan cache** holds validated, executed plans keyed by `(normalised
sub-query intent, run date and planner model, datasource_id, schema_version)`
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
came later; migration 3 adds the immutability trigger and the `locks` table.

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
build was made from, and switching that pointer is the commit. Every reader
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
- The `StateStore` interface: `packages/sdk/src/nl2sql_sdk/stores.py`
- The `sqlite` state store: `packages/nl2sql/src/nl2sql/storage/sqlite.py`
- Which snapshots are kept: `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- The index lock: `packages/nl2sql/src/nl2sql/indexing/rebuild.py`
- Schema retriever: `packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
