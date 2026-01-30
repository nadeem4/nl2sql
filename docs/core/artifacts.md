# Executor Artifacts

## Overview

Executors now write scan results as Parquet artifacts. The aggregator reads those artifacts and executes the `ExecutionDAG` layers without recomputing topological order.

## Artifact path

Default template:

`<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet`

## Backends

- Local filesystem
- S3
- ADLS

## Executor write path

Executor artifact writes use Polars to serialize Parquet files.

## Executor routing

Executor services are resolved via an `ExecutorRegistry` based on datasource capabilities (e.g., `supports_sql`).

Configure via environment variables in `Settings`:

- `RESULT_ARTIFACT_BACKEND`
- `RESULT_ARTIFACT_BASE_URI`
- `RESULT_ARTIFACT_PATH_TEMPLATE`
- `RESULT_ARTIFACT_S3_BUCKET`, `RESULT_ARTIFACT_S3_PREFIX`
- `RESULT_ARTIFACT_ADLS_ACCOUNT`, `RESULT_ARTIFACT_ADLS_CONTAINER`, `RESULT_ARTIFACT_ADLS_CONNECTION_STRING`
