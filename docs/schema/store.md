# Schema & Metadata Layer

The schema layer provides **authoritative, versioned snapshots** and **structured retrieval** to ground planning. It is split into:

- **Schema contracts**: structural schema (tables, columns, foreign keys).
- **Schema metadata**: descriptive enrichment and statistics.
- **Schema store**: versioned persistence with fingerprinting.
- **Schema retrieval**: staged lookup via schema chunks.

## Schema contracts and metadata

Schema contracts are defined in `nl2sql_adapter_sdk.schema`:

- `SchemaContract`: datasource_id, engine_type, tables
- `TableContract`: table ref, column contracts, foreign keys
- `ColumnContract`: name, type, nullability, primary key flag
- `ForeignKeyContract`: constrained/referred columns, cardinality, business meaning

Metadata complements contracts:

- `SchemaMetadata`: datasource description/domains, table metadata
- `TableMetadata`: table description, row_count, column metadata
- `ColumnMetadata`: description, stats, synonyms, PII flag
- `ColumnStatistics`: distinct counts, min/max, sample values

## Snapshotting and fingerprinting

Each `SchemaSnapshot` (contract + metadata) is versioned using a **deterministic fingerprint**:

- Fingerprint uses datasource ID, engine type, sorted tables, sorted columns, and sorted foreign keys.
- Resulting hash is used to deduplicate identical snapshots.

## Schema store backends

`build_schema_store()` constructs a store based on settings:

- `InMemorySchemaStore`: in-memory, versioned snapshots.
- `SqliteSchemaStore`: persistent storage with indexes on fingerprint and timestamps.

Schema versions are timestamped and include a fingerprint prefix (e.g., `YYYYMMDDhhmmss_<fp8>`). Old versions are evicted beyond `schema_store_max_versions`.

## Retrieval and authority

Schema retrieval resolves **authoritative** tables/columns from `SchemaStore`, not from the vector store. Vector store chunks are only used to identify candidates; final schema is resolved via snapshot. If retrieval yields no candidates, the retriever falls back to the full snapshot. `schema_version_mismatch_policy` governs mismatches between chunk versions and store versions.

See `../architecture/indexing.md` for retrieval stages, chunk types, and vector store behavior.

## Source references

- Contracts and metadata: `packages/adapter-sdk/src/nl2sql_adapter_sdk/schema.py`
- Fingerprinting: `packages/core/src/nl2sql/schema/protocol.py`
- Schema store factory: `packages/core/src/nl2sql/schema/store.py`
- Sqlite store: `packages/core/src/nl2sql/schema/sqlite_store.py`
- Schema retriever: `packages/core/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
