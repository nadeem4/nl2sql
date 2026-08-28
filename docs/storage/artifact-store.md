# Artifact Store Architecture

Execution results are persisted as **artifacts**. A single `ArtifactStore` writes Parquet to the configured backend (local, S3, ADLS) and emits `ArtifactRef` metadata used by aggregation and downstream consumers.

## Storage lifecycle

```mermaid
flowchart TD
    ResultFrame[ResultFrame] --> Store[ArtifactStore.create_artifact_ref]
    Store --> Parquet[Parquet Object]
    Parquet --> Ref[ArtifactRef]
    Ref --> Aggregation[EngineAggregatorNode]
```

## One store, three URI schemes

There is no per-backend subclass. `ArtifactStore` builds a backend-specific URI, then uses the same polars read/write path for all backends, since polars addresses `s3://` and `abfs://` natively.

| Backend | URI built from settings |
| --- | --- |
| `local` | `<result_artifact_base_uri>/<rendered path>` (parent directories are created) |
| `s3` | `s3://<s3_bucket>/<s3_prefix>/<rendered path>` |
| `adls` | `abfs://<adls_container>@<adls_account>.dfs.core.windows.net/<rendered path>` |

Missing required configuration raises a `ValueError` naming the environment variable to set — `RESULT_ARTIFACT_S3_BUCKET`, `RESULT_ARTIFACT_ADLS_CONTAINER`, or `RESULT_ARTIFACT_ADLS_ACCOUNT`. An unrecognised `RESULT_ARTIFACT_BACKEND` also raises rather than silently falling back to local.

For ADLS, `RESULT_ARTIFACT_ADLS_CONNECTION_STRING` is forwarded to polars as `storage_options={"connection_string": ...}`. No Azure SDK client is constructed. S3 credentials are resolved by the underlying object-store layer from the usual environment.

## Verification status

- **Local** is exercised by real read/write round-trip tests (`packages/nl2sql/tests/unit/test_artifact_store.py`).
- **S3 and ADLS** are implemented and unit-tested at the URI and `storage_options` level, with the actual object-store IO mocked. They have **not** been verified against a real S3 bucket or ADLS account. Treat them as unproven until someone runs them against live storage.

## ArtifactRef fields

`ArtifactRef` contains:

- `uri`, `backend`, `format`
- `row_count`, `columns`, `bytes`
- `content_hash`, `created_at`
- optional `schema_version`
- `path_template`

## Path templating

`Settings.result_artifact_path_template` defines the artifact path relative to the backend root, for every backend. It defaults to:

```
<tenant_id>/<request_id>.parquet
```

Each `<key>` placeholder is substituted from the metadata the executor passes to `create_artifact_ref`. The SQL executor supplies exactly three keys:

- `tenant_id`
- `request_id` (the trace ID)
- `schema_version`

A template referencing any other placeholder — for example `<subgraph_name>` or `<dag_node_id>` — raises a `ValueError` naming the placeholder that could not be filled. A path is never written with an unrendered placeholder in it. Adding placeholders therefore requires threading the corresponding metadata through the executor first.

## Tenant-aware paths

Because the default template starts with `<tenant_id>`, every backend partitions artifacts per tenant:

```
<backend root>/<tenant_id>/<request_id>.parquet
```

## Source references

- Artifact store: `packages/nl2sql/src/nl2sql/execution/artifacts/store.py`
- Parquet helpers: `packages/nl2sql/src/nl2sql/execution/artifacts/parquet.py`
- Artifact contracts: `packages/nl2sql/src/nl2sql/execution/contracts.py`
