# Multi-Tenant Isolation Model

Tenant separation is enforced via **context propagation**, **artifact partitioning**, and **policy filtering**. The tenant identifier originates in `Settings.tenant_id` and can be overridden per request via `UserContext`.

## Tenant context propagation

```mermaid
flowchart TD
    Settings[Settings.tenant_id] --> Context[NL2SQLContext.tenant_id]
    Context --> GraphState[GraphState.user_context]
    GraphState --> Executor[ExecutorNode/ExecutorRequest]
    Executor --> Artifacts[ArtifactStore.get_upload_path]
    GraphState --> Logger[tenant_context()]
```

## Storage isolation

`ArtifactStore.get_upload_path()` is tenant-aware. The local artifact backend nests artifacts under `<base_uri>/<tenant_id>/...`.

## Authorization model

`RBAC` uses `UserContext.roles` with `RolePolicy` definitions loaded from `configs/policies.json`. The registry is initialized in `NL2SQLContext` and consulted by nodes that require policy enforcement.

## Source references

- Tenant settings: `packages/core/src/nl2sql/common/settings.py`
- Context propagation: `packages/core/src/nl2sql/context.py`
- Logger tenant context: `packages/core/src/nl2sql/common/logger.py`
- Artifact store paths: `packages/core/src/nl2sql/execution/artifacts/base.py`
- RBAC: `packages/core/src/nl2sql/auth/rbac.py`
