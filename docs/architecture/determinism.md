# Determinism Architecture

## Overview
- Determinism matters because the pipeline composes multi-step planning, execution, and aggregation; stable identifiers and ordering are required for reproducible DAGs, consistent merges, and auditability.
- The system implements determinism in specific places (hashing, sorting, and schema fingerprinting) but also contains explicit nondeterminism (LLM outputs, vector retrieval ranking, timestamps, random retry jitter, and external calls).
- Scope: planning (IDs + DAG), retrieval (vector ordering), execution ordering (DAG layers), artifacts (content hashing), and state merges.

---

## Determinism Domains

### Inputs and Identifier Stability
- Deterministic: Sub-query and post-combine op IDs are derived from sorted JSON payloads with SHA-256 ([`pipeline/nodes/decomposer/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py)).
- Non-deterministic: `GraphState.trace_id` defaults to `uuid.uuid4()`; subgraph IDs incorporate this value ([`pipeline/state.py`](../../packages/core/src/nl2sql/pipeline/state.py), [`pipeline/graph_utils.py`](../../packages/core/src/nl2sql/pipeline/graph_utils.py)), so identical inputs produce different IDs unless a trace ID is supplied.
- Non-deterministic: User query interpretation depends on LLMs in the decomposer, planner, and refiner nodes, with no local temperature/seed control visible in these nodes ([`pipeline/nodes/decomposer/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/ast_planner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/ast_planner/node.py), [`pipeline/nodes/refiner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/refiner/node.py)).

### Planner and DAG Construction
- Deterministic: Global planner sorts nodes and edges by IDs and roles before constructing the DAG, then hashes a sorted JSON payload to produce a stable `dag_id` for a given logical plan ([`pipeline/nodes/global_planner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/global_planner/node.py)).
- Deterministic: DAG layers are computed with a topological sort that sorts ready nodes and dependents, yielding stable layer ordering given the same node/edge sets ([`pipeline/nodes/global_planner/schemas.py`](../../packages/core/src/nl2sql/pipeline/nodes/global_planner/schemas.py)).
- Non-deterministic: The AST planner is LLM-driven; the PlanModel content is not stabilized inside the node ([`pipeline/nodes/ast_planner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/ast_planner/node.py)).

### Retrieval Ordering and Chunk Selection
- Non-deterministic: Vector retrieval uses max marginal relevance search; ranking and ties depend on vector store behavior and similarity scores, with no secondary deterministic tie-breaker in code ([`indexing/vector_store.py`](../../packages/core/src/nl2sql/indexing/vector_store.py)).
- Non-deterministic: Datasource resolution uses vector retrieval results directly; candidate ordering follows the retrieval output and is not re-sorted ([`pipeline/nodes/datasource_resolver/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/datasource_resolver/node.py)).
- Non-deterministic: Schema retrieval builds the table/column set from vector retrieval metadata and uses dictionary/set iteration without sorting; final `relevant_tables` ordering follows input ordering ([`pipeline/nodes/schema_retriever/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/schema_retriever/node.py)).
- Partially deterministic: Table chunk column names are sorted for the table chunk payload, but chunk emission order depends on the schema contract’s insertion order (no sorting over `tables.items()` or `columns.items()` for column chunks) ([`indexing/chunk_builder.py`](../../packages/core/src/nl2sql/indexing/chunk_builder.py)).

### Schema Authority and Versioning
- Deterministic: Schema fingerprints are computed from sorted tables, columns, and foreign keys with JSON serialization; same schema contract yields the same fingerprint ([`schema/protocol.py`](../../packages/core/src/nl2sql/schema/protocol.py)).
- Deterministic: Both in-memory and SQLite schema stores deduplicate by fingerprint, returning an existing version if one matches ([`schema/in_memory_store.py`](../../packages/core/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](../../packages/core/src/nl2sql/schema/sqlite_store.py)).
- Non-deterministic: Schema versions are time-based (`YYYYMMDDhhmmss_<fingerprint>`). Latest version selection depends on registration time and order ([`schema/in_memory_store.py`](../../packages/core/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](../../packages/core/src/nl2sql/schema/sqlite_store.py)).
- Deterministic (conditional): If `schema_version` is provided by a sub-query, schema retrieval resolves that exact snapshot; otherwise it uses the latest available version, which is time-ordered ([`pipeline/nodes/schema_retriever/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/schema_retriever/node.py)).

### Validation Gates
- Deterministic: Logical validation normalizes names, enforces ordinal continuity, validates aliases, and uses sorted comparisons for expected schema/alias matching ([`pipeline/nodes/validator/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/validator/node.py)).
- Deterministic: Policy enforcement uses explicit namespaced checks and deterministic set membership ([`pipeline/nodes/validator/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/validator/node.py)).

### DAG Execution Order and Node Sequencing
- Deterministic: Aggregation executes layer-by-layer in DAG order, with ordered inputs computed by role rank and node ID, and terminal nodes sorted by ID ([`aggregation/aggregator.py`](../../packages/core/src/nl2sql/aggregation/aggregator.py)).
- Deterministic: Router selects the next scan layer based on the DAG layers and existing artifact refs ([`pipeline/graph_utils.py`](../../packages/core/src/nl2sql/pipeline/graph_utils.py), [`pipeline/routes.py`](../../packages/core/src/nl2sql/pipeline/routes.py)).

### Subgraph Composition and Routing
- Partially deterministic: Subgraph selection chooses the first matching spec in dictionary iteration order; if multiple subgraphs satisfy capabilities, selection depends on insertion order of `subgraph_specs` ([`pipeline/graph_utils.py`](../../packages/core/src/nl2sql/pipeline/graph_utils.py)).
- Deterministic (conditional): Scan payloads and subgraph outputs are built from explicit state fields; content is deterministic given the input state ([`pipeline/graph_utils.py`](../../packages/core/src/nl2sql/pipeline/graph_utils.py)).

### Retry Mechanisms and Backoff
- Non-deterministic: Retry backoff includes random jitter; sleep time is a function of random.uniform and wall-clock timing ([`pipeline/subgraphs/sql_agent.py`](../../packages/core/src/nl2sql/pipeline/subgraphs/sql_agent.py)).
- Deterministic (conditional): Retry routing decisions depend on current retry count and error retryability flags; deterministic if state is unchanged ([`pipeline/subgraphs/sql_agent.py`](../../packages/core/src/nl2sql/pipeline/subgraphs/sql_agent.py)).

### Artifact Storage and Hashing
- Deterministic: Local artifact content hashes are computed from a sorted JSON payload (columns, row_count, path), giving stable hashes for the same payload ([`execution/artifacts/local_store.py`](../../packages/core/src/nl2sql/execution/artifacts/local_store.py)).
- Non-deterministic: Artifact refs include `created_at=datetime.utcnow()`; this is time-based and changes for each creation ([`execution/artifacts/base.py`](../../packages/core/src/nl2sql/execution/artifacts/base.py)).
- Deterministic (conditional): Upload paths are deterministic given `tenant_id` and `request_id`, but the request ID origin is external to this module ([`execution/artifacts/local_store.py`](../../packages/core/src/nl2sql/execution/artifacts/local_store.py)).

### State Mutation and Merge Semantics
- Deterministic: Decomposer returns sorted sub-queries, combine groups, and post-combine ops by ID to stabilize downstream ordering ([`pipeline/nodes/decomposer/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py)).
- Potentially non-deterministic: GraphState merges dict fields with a last-write-wins reducer and concatenates lists; in parallel branches, merge order is not constrained in this code ([`pipeline/state.py`](../../packages/core/src/nl2sql/pipeline/state.py)).

### Hashing/Fingerprinting
- Deterministic: Stable hashing is consistently performed with sorted JSON and fixed separators for sub-query IDs, DAG hashes, schema fingerprints, and artifact content hashes ([`pipeline/nodes/decomposer/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/global_planner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/global_planner/node.py), [`schema/protocol.py`](../../packages/core/src/nl2sql/schema/protocol.py), [`execution/artifacts/local_store.py`](../../packages/core/src/nl2sql/execution/artifacts/local_store.py)).

### Time-Based Logic and Runtime Controls
- Non-deterministic: Schema versions and artifact creation timestamps are derived from wall-clock time ([`schema/in_memory_store.py`](../../packages/core/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](../../packages/core/src/nl2sql/schema/sqlite_store.py), [`execution/artifacts/base.py`](../../packages/core/src/nl2sql/execution/artifacts/base.py)).
- Deterministic (conditional): Pipeline timeout handling is based on monotonic time; timeouts and cancellations depend on wall-clock progression and runtime scheduling ([`pipeline/runtime.py`](../../packages/core/src/nl2sql/pipeline/runtime.py)).

### External Calls
- Non-deterministic: LLM-driven nodes (decomposer, planner, refiner) invoke external LLMs without deterministic configuration in these nodes ([`pipeline/nodes/decomposer/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/ast_planner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/ast_planner/node.py), [`pipeline/nodes/refiner/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/refiner/node.py)).
- Non-deterministic: Vector store retrieval depends on external embeddings and search behavior ([`indexing/vector_store.py`](../../packages/core/src/nl2sql/indexing/vector_store.py)).
- Non-deterministic: SQL execution delegates to external executors and underlying databases, which can return different results across time or state ([`pipeline/nodes/executor/node.py`](../../packages/core/src/nl2sql/pipeline/nodes/executor/node.py)).
