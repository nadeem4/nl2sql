# Multi-Tenant Isolation Model

Multi-tenancy is enforced through **context propagation**, **artifact partitioning**, and **policy filtering**. Tenant identity originates from `Settings.tenant_id` and is passed through execution requests and artifact paths.

## Tenant context propagation

```mermaid
flowchart TD
    Settings[Settings.tenant_id] --> Context[NL2SQLContext.tenant_id]
    Context --> Executor[ExecutorNode/ExecutorRequest]
    Executor --> Artifacts[ArtifactStore.create_artifact_ref]
```

## Storage isolation

Artifacts are persisted under a tenant-partitioned path on every backend, driven by `RESULT_ARTIFACT_PATH_TEMPLATE` (default `<tenant_id>/<request_id>.parquet`):

```
<backend root>/<tenant_id>/<request_id>.parquet
```

This ensures per-tenant isolation for artifacts, and downstream aggregation only reads referenced artifacts from the current request.

Tenant isolation is **not** applied to indexing; the vector store and schema store are global in the current implementation (see `../architecture/indexing.md`).

## Authorization model

RBAC evaluates `UserContext.roles` against policies defined in `configs/policies.json`. The validator enforces datasource/table scope at planning time.

## Source references

- Tenant settings: `packages/core/src/nl2sql/common/settings.py`
- Context initialization: `packages/core/src/nl2sql/context.py`
- Artifact paths: `packages/core/src/nl2sql/execution/artifacts/store.py`
- RBAC: `packages/core/src/nl2sql/auth/rbac.py`
