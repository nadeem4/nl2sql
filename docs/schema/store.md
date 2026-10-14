# Schema & Metadata Layer

The schema layer provides **authoritative, versioned snapshots** and **structured retrieval** to ground planning. It is split into:

- **Schema contracts**: structural schema (tables, columns, foreign keys).
- **Schema metadata**: descriptive enrichment and statistics.
- **Schema store**: versioned persistence with fingerprinting.
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
  not profile has no statistics; see
  [Column statistics](../adapters/sqlalchemy.md#column-statistics)

## Snapshotting and fingerprinting

Each `SchemaSnapshot` (contract + metadata) is versioned using a **deterministic fingerprint**:

- Fingerprint uses datasource ID, engine type, sorted tables, sorted columns, and sorted foreign keys.
- Resulting hash is used to deduplicate identical snapshots.

## Schema store backends

`build_schema_store()` constructs a store based on settings:

- `InMemorySchemaStore`: in-memory, versioned snapshots.
- `SqliteSchemaStore`: persistent storage with indexes on fingerprint and timestamps. One connection serves every thread, and each use of it holds one lock, so a server reading the schema and the index health on two workers at once is safe.

Schema versions are timestamped and include a fingerprint prefix (e.g., `YYYYMMDDhhmmss_<fp8>`). Old versions are evicted beyond `schema_store_max_versions`.

The store also holds the **plan cache**: validated, executed plans keyed by `(normalised sub-query intent, run date and planner model, datasource_id, schema_version)`, in the `plan_cache` table of the SQLite store (a dict in the in-memory one). Evicting a schema version deletes its cached plans; `nl2sql cache clear` deletes them all and keeps the snapshots. See [Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

The same SQLite file holds the playground's answer ratings, in a `feedback` table (one row per rated run; questions and SQL, never rows). It is written whatever `SCHEMA_STORE_BACKEND` is, at `SCHEMA_STORE_PATH`; `nl2sql feedback clear` empties it and nothing else. See [Feedback and Signals](../observability/feedback.md).

The fingerprint covers **structure only** (tables, columns, keys). Registering a snapshot whose structure matches an existing version returns that version unchanged, so anything keyed on it (the plan cache) stays valid, but the stored **metadata** (statistics, descriptions, enrichment) is replaced by the new snapshot's. The snapshot store and the chunks built from the same snapshot therefore always agree.

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

Schema retrieval resolves **authoritative** tables/columns from `SchemaStore`, not from the index. Index entries are only used to identify candidates; final schema is resolved via the snapshot the live pointer names. If retrieval yields no candidates, the retriever falls back to the full snapshot. The entries and the snapshot come from the same build, so their versions cannot disagree.

See `../architecture/indexing.md` for retrieval stages, chunk types, and vector store behavior.

## Source references

- Contracts and metadata: `packages/sdk/src/nl2sql_sdk/schema.py`
- Fingerprinting: `packages/nl2sql/src/nl2sql/schema/protocol.py`
- Schema store factory: `packages/nl2sql/src/nl2sql/schema/store.py`
- Sqlite store: `packages/nl2sql/src/nl2sql/schema/sqlite_store.py`
- Schema retriever: `packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
