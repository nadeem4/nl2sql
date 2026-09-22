# Determinism Architecture

## What "deterministic" means here

The word is used narrowly and it does not mean "the same question gives the same
answer". **Model output is not reproducible.** What is stable is the shape of a
run:

- **Stable sub-query and DAG ids.** Both are SHA-256 content hashes over
  canonical JSON, not counters or UUIDs ([`decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py), [`global_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/global_planner/node.py)). The same decomposition always yields the same ids.
- **Sorted layer order.** The topological sort sorts each ready set and each
  dependent set, so the execution layers of a given DAG are fixed
  ([`global_planner/schemas.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/global_planner/schemas.py)).
- **Fixed graph topology.** The node sequence is compiled, not chosen by the
  model; only the retry loop varies, and only in how many times it runs.
- **Validation before generation.** The AST is checked against the retrieved
  schema and the RBAC policy before any SQL exists, on every path.

Everything else is best effort:

- `temperature` defaults to `0.0` and `seed=42` is pinned when the client is
  built ([`llm/registry.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/llm/registry.py)).
  An agent configured with `temperature: null` sends no temperature at all,
  which models such as `gpt-5.5` and `gpt-5-mini` require (they reject `0` with
  HTTP 400); those agents run at the model's default temperature, so their
  variance is higher. See [LLM configuration → Temperature](../configuration/llm.md#temperature). The seed is sent to every provider because all three
  speak the OpenAI protocol, but **only OpenAI interprets it, and even there it
  is documented as best effort**; OpenRouter and Ollama ignore it. Temperature 0
  is not a reproducibility guarantee either — it reduces variance, it does not
  remove it.
- Vector retrieval ranking, wall-clock schema versions, artifact timestamps and
  retry jitter are all non-deterministic; the sections below say exactly where.

If you need a byte-identical run, record the model's responses and replay them
(`nl2sql/llm/replay.py`); that is what the key-free demo mode is built on.

## The plan cache: determinism from the architecture

The model cannot be made deterministic (`gpt-5.5` rejects `temperature: 0`, and
`seed` is best effort), so repeat determinism comes from the pipeline instead.
The AST planner is the LLM call that decides the SQL; the validator, generator
and executor after it are deterministic code. A plan that passed validation and
executed is therefore **pinned** and reused
([`pipeline/plan_cache.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/plan_cache.py)):

- **Where it sits.** At the AST planner, per sub-query. The decomposer turns the
  question into sub-queries, and the planner plans each one from its `intent`,
  `datasource_id` and the schema snapshot at `schema_version`. Caching there
  reuses exactly the output that decides the SQL, and a multi-datasource
  question reuses each of its sub-queries' plans independently.
- **The key.** `(normalised sub-query intent, datasource_id, schema_version)`.
  Normalisation is case-folding, collapsing whitespace and stripping trailing
  `.?!,;:`, and nothing else; matching is exact, never by similarity. A
  sub-query without a `schema_version` is never cached.
- **Invalidation.** Automatic through the schema version: a re-index with a
  changed schema registers a new version, which misses. Plans for a version the
  store evicts are deleted with it. There is no TTL and no LRU.
- **Always re-validated.** A hit replaces only the planner's LLM call (and so
  the refiner, which only runs after a rejected plan). The logical validator,
  generator and executor run on the cached plan every time, so a plan cached for
  `admin` is still refused for `viewer`, and a policy change applies to the next
  request. Only a first attempt reads the cache; a retry after a rejected plan
  always asks the model.
- **What is stored.** Only plans that passed validation *and* executed without
  error, written by the sub-query wrapper
  ([`pipeline/graph_utils.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/graph_utils.py)).
  A refused plan, a failed plan and a plan-only (`--no-exec`) run are never
  stored.
- **Where it is stored.** The `plan_cache` table of the schema store
  (`SCHEMA_STORE_PATH`, `data/schema_store.db` by default); the `memory` backend
  keeps it for the life of the process.
- **Controls.** `PLAN_CACHE_ENABLED=false` turns reads and writes off
  ([System configuration](../configuration/system.md)); `nl2sql cache clear`
  empties it. `nl2sql demo --record` turns it off so every planner answer is
  recorded, and `nl2sql trace replay` uses it only when the recorded run did.
- **Visible.** `QueryResult.sub_queries[].plan_source` is `"cache"` or `"llm"`,
  `QueryResult.usage.plan_cache_hits` counts hits (a hit adds no planner
  tokens), the trace's `ast_planner` execution shows
  `ast_planner_response.plan_source: "cache"` with no LLM calls, and the
  `nl2sql.plan_cache.lookups` counter records hits and misses
  ([Debugging](../observability/debugging.md)).

What the cache does not pin: the decomposer still calls the model, so a repeat
hits only when the decomposer produces the same sub-query intent (after
normalisation). A differently worded intent is a miss and is planned afresh.
Given a hit, the SQL is identical, and so are the rows, because the generator
gives every result a total row order.

## Overview
- Determinism matters because the pipeline composes multi-step planning, execution, and aggregation; stable identifiers and ordering are required for reproducible DAGs, consistent merges, and auditability.
- The system implements determinism in specific places (hashing, sorting, and schema fingerprinting) but also contains explicit nondeterminism (LLM outputs, vector retrieval ranking, timestamps, random retry jitter, and external calls).
- Scope: planning (IDs + DAG), retrieval (vector ordering), execution ordering (DAG layers), artifacts (content hashing), and state merges.

---

## Determinism Domains

### Inputs and Identifier Stability
- Deterministic: Sub-query and post-combine op IDs are derived from sorted JSON payloads with SHA-256 ([`pipeline/nodes/decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py)).
- Non-deterministic: `GraphState.trace_id` defaults to `uuid.uuid4()`; subgraph IDs incorporate this value ([`pipeline/state.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/state.py), [`pipeline/graph_utils.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/graph_utils.py)), so identical inputs produce different IDs unless a trace ID is supplied.
- Non-deterministic: User query interpretation depends on LLMs in the decomposer, planner, and refiner nodes. These nodes set nothing themselves; `temperature` (default `0.0`, omitted when configured as `null`) and `seed=42` are applied once, where the client is built in [`llm/registry.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/llm/registry.py), and neither makes the output reproducible ([`pipeline/nodes/decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/ast_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/ast_planner/node.py), [`pipeline/nodes/refiner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/refiner/node.py)).

### Planner and DAG Construction
- Deterministic: Global planner sorts nodes and edges by IDs and roles before constructing the DAG, then hashes a sorted JSON payload to produce a stable `dag_id` for a given logical plan ([`pipeline/nodes/global_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/global_planner/node.py)).
- Deterministic: DAG layers are computed with a topological sort that sorts ready nodes and dependents, yielding stable layer ordering given the same node/edge sets ([`pipeline/nodes/global_planner/schemas.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/global_planner/schemas.py)).
- Non-deterministic: The AST planner is LLM-driven; the PlanModel content is not stabilized inside the node ([`pipeline/nodes/ast_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/ast_planner/node.py)).
- Deterministic (conditional): Once a sub-query's plan has validated and executed, the plan cache pins it: the same normalised intent, datasource and schema version reuse that plan without a planner call, and it is validated again on every use (see [The plan cache](#the-plan-cache-determinism-from-the-architecture)).
- Deterministic: Generated SQL always has a total row order. The generator always appends `LIMIT`, so it also orders by every selected column: after the plan's own `ORDER BY` terms, the remaining selected columns follow as ascending tie-breakers, in select order. Aliased items are ordered by alias, never by position, and constants are skipped. Given the same plan and data, a truncated result is always the same rows in the same order ([`pipeline/nodes/generator/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/generator/node.py)).

### Retrieval Ordering and Chunk Selection
- Non-deterministic: Vector retrieval uses max marginal relevance search; ranking and ties depend on vector store behavior and similarity scores, with no secondary deterministic tie-breaker in code ([`indexing/vector_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/indexing/vector_store.py)).
- Non-deterministic: Datasource resolution uses vector retrieval results directly; candidate ordering follows the retrieval output and is not re-sorted ([`pipeline/nodes/datasource_resolver/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/datasource_resolver/node.py)).
- Non-deterministic: Schema retrieval builds the table/column set from vector retrieval metadata and uses dictionary/set iteration without sorting; final `relevant_tables` ordering follows input ordering ([`pipeline/nodes/schema_retriever/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py)).
- Partially deterministic: Table chunk column names are sorted for the table chunk payload, but chunk emission order depends on the schema contract’s insertion order (no sorting over `tables.items()` or `columns.items()` for column chunks) ([`indexing/chunk_builder.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/indexing/chunk_builder.py)).

### Schema Authority and Versioning
- Deterministic: Schema fingerprints are computed from sorted tables, columns, and foreign keys with JSON serialization; same schema contract yields the same fingerprint ([`schema/protocol.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/protocol.py)).
- Deterministic: Both in-memory and SQLite schema stores deduplicate by fingerprint, returning an existing version if one matches; the metadata stored under that version is refreshed from the new snapshot ([`schema/in_memory_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/sqlite_store.py)).
- Non-deterministic: Schema versions are time-based (`YYYYMMDDhhmmss_<fingerprint>`). Latest version selection depends on registration time and order ([`schema/in_memory_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/sqlite_store.py)).
- Deterministic (conditional): If `schema_version` is provided by a sub-query, schema retrieval resolves that exact snapshot; otherwise it uses the latest available version, which is time-ordered ([`pipeline/nodes/schema_retriever/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/schema_retriever/node.py)).

### Validation Gates
- Deterministic: Logical validation normalizes names, enforces ordinal continuity, validates aliases, and uses sorted comparisons for expected schema/alias matching ([`pipeline/nodes/validator/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/validator/node.py)).
- Deterministic: Policy enforcement uses explicit namespaced checks and deterministic set membership ([`pipeline/nodes/validator/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/validator/node.py)).

### DAG Execution Order and Node Sequencing
- Deterministic: Aggregation executes layer-by-layer in DAG order, with ordered inputs computed by role rank and node ID, and terminal nodes sorted by ID ([`aggregation/aggregator.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/aggregation/aggregator.py)).
- Deterministic: Router selects the next scan layer based on the DAG layers and existing artifact refs ([`pipeline/graph_utils.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/graph_utils.py), [`pipeline/routes.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/routes.py)).

### Subgraph Composition and Routing
- Deterministic: Subgraph selection has a single outcome -- `sql_agent` when the datasource declares `supports_sql`, otherwise no subgraph ([`pipeline/graph_utils.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/graph_utils.py)).
- Deterministic (conditional): Scan payloads and subgraph outputs are built from explicit state fields; content is deterministic given the input state ([`pipeline/graph_utils.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/graph_utils.py)).

### Retry Mechanisms and Backoff
- Non-deterministic: Retry backoff includes random jitter; sleep time is a function of random.uniform and wall-clock timing ([`pipeline/subgraphs/sql_agent.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/subgraphs/sql_agent.py)).
- Deterministic (conditional): Retry routing decisions depend on current retry count and error retryability flags; deterministic if state is unchanged ([`pipeline/subgraphs/sql_agent.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/subgraphs/sql_agent.py)).

### Artifact Storage and Hashing
- Deterministic: Artifact content hashes are computed from a sorted JSON payload (columns, row_count, path), giving stable hashes for the same payload ([`execution/artifacts/store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/execution/artifacts/store.py)).
- Non-deterministic: Artifact refs include `created_at=datetime.utcnow()`; this is time-based and changes for each creation ([`execution/artifacts/store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/execution/artifacts/store.py)).
- Deterministic (conditional): Upload paths are deterministic given `tenant_id` and `request_id`, but the request ID origin is external to this module ([`execution/artifacts/store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/execution/artifacts/store.py)).

### State Mutation and Merge Semantics
- Deterministic: Decomposer returns sorted sub-queries, combine groups, and post-combine ops by ID to stabilize downstream ordering ([`pipeline/nodes/decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py)).
- Potentially non-deterministic: GraphState merges dict fields with a last-write-wins reducer and concatenates lists; in parallel branches, merge order is not constrained in this code ([`pipeline/state.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/state.py)).

### Hashing/Fingerprinting
- Deterministic: Stable hashing is consistently performed with sorted JSON and fixed separators for sub-query IDs, DAG hashes, schema fingerprints, and artifact content hashes ([`pipeline/nodes/decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/global_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/global_planner/node.py), [`schema/protocol.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/protocol.py), [`execution/artifacts/store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/execution/artifacts/store.py)).

### Time-Based Logic and Runtime Controls
- Non-deterministic: Schema versions and artifact creation timestamps are derived from wall-clock time ([`schema/in_memory_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/in_memory_store.py), [`schema/sqlite_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/schema/sqlite_store.py), [`execution/artifacts/store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/execution/artifacts/store.py)).
- Deterministic (conditional): Pipeline timeout handling is based on monotonic time; timeouts and cancellations depend on wall-clock progression and runtime scheduling ([`pipeline/runtime.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/runtime.py)).

### External Calls
- Non-deterministic: LLM-driven nodes (decomposer, planner, refiner) invoke external LLMs. The client is pinned to `seed=42` and the configured temperature (`0.0` unless an agent sets `temperature: null`) in `llm/registry.py`, which narrows the variance without eliminating it ([`pipeline/nodes/decomposer/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/node.py), [`pipeline/nodes/ast_planner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/ast_planner/node.py), [`pipeline/nodes/refiner/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/refiner/node.py)).
- Non-deterministic: Vector store retrieval depends on external embeddings and search behavior ([`indexing/vector_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/indexing/vector_store.py)).
- Non-deterministic: SQL execution delegates to external executors and underlying databases, which can return different results across time or state ([`pipeline/nodes/executor/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/executor/node.py)).
