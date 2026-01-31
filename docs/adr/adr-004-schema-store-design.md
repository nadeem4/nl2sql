# ADR-004: Schema Store Design and Fingerprinting

## Status

Accepted (implemented in `SqliteSchemaStore` and `InMemorySchemaStore`).

## Context

The system needs an authoritative, versioned view of each datasource schema. Vector indexes may drift or be stale, so planning must reference a canonical schema snapshot.

## Decision

Store schema snapshots with **deterministic fingerprints**:

- `SchemaContract` content is hashed to produce a stable fingerprint.
- Snapshots are versioned using timestamp + fingerprint prefix.
- Older versions are evicted beyond a configurable maximum.

Persistent storage is provided by a SQLite-backed schema store, with an in-memory alternative for testing.

## Consequences

- Schema versions are stable and deduplicated.
- Retrieval uses authoritative snapshots even if vector chunks drift.
- The system can enforce version mismatch policies.

## Source references

- Fingerprinting: `packages/core/src/nl2sql/schema/protocol.py`
- Sqlite store: `packages/core/src/nl2sql/schema/sqlite_store.py`
- In-memory store: `packages/core/src/nl2sql/schema/in_memory_store.py`
