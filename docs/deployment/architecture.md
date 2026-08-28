# Deployment Architecture

NL2SQL is a Python engine that you embed in a service of your choice. The runtime is a single process that initializes `NL2SQLContext`, loads configuration, and invokes the pipeline per request.

## Runtime process layout

```mermaid
flowchart TD
    App[Service Process] --> Context[NL2SQLContext]
    Context --> Registries[Registries + Stores]
    Context --> Graph[LangGraph Pipeline]
    Graph --> Exec[Executor Services]
    Exec --> DB[(Datasource)]
```

## Deployment inputs

- Environment variables for `Settings` (paths and runtime parameters).
- Configuration files under `configs/`.
- Adapter packages installed in the runtime environment.
- Optional persistence volumes for vector store, schema store, and artifacts.

## Runtime modes

- **Local dev**: local vector store + SQLite schema store + local artifact store.
- **Production**: persistent volumes or external stores, stable secret providers, observability exporter enabled.

## Scaling and isolation

- Orchestration runs in-process and can scale horizontally with your service.
- There is no process-level sandbox. The graph runs on a thread pool inside your service process (`SANDBOX_EXEC_WORKERS` sizes it), so a driver-level crash takes the process down. If you need blast-radius containment, isolate at the deployment boundary — a replica or container you are willing to lose — and let your supervisor restart it. See `../execution/isolation.md`.

## Source references

- Context initialization: `packages/nl2sql/src/nl2sql/context.py`
- Settings: `packages/nl2sql/src/nl2sql/common/settings.py`
