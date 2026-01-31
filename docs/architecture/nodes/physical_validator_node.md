# PhysicalValidatorNode

## Overview

- Performs dry‑run and cost‑estimate checks on generated SQL using sandboxed execution.
- Exists to validate executability and performance limits before execution.
- Not wired in the default SQL agent graph.
- Class: `PhysicalValidatorNode`
- Source: `packages/core/src/nl2sql/pipeline/nodes/validator/physical_node.py`

---

## Responsibilities

- Run dry‑run validation via adapter `dry_run()`.
- Run cost estimation via adapter `cost_estimate()`.
- Use sandboxed execution pool and `DB_BREAKER` for isolation and resilience.

---

## Position in Execution Graph

Upstream:
- None (not connected in current graph).

Downstream:
- None (not connected in current graph).

Trigger conditions:
- Not executed in current pipeline; requires graph wiring.

```mermaid
flowchart LR
    PhysicalValidator[PhysicalValidatorNode]
```

---

## Inputs

From `SubgraphExecutionState`:

- `generator_response.sql_draft` (required)
- `sub_query.datasource_id` (required)

From `NL2SQLContext`:

- `ds_registry` (adapter resolution)

Validation performed:

- If `sql` is missing, returns empty response.
- If `datasource_id` missing, emits `MISSING_DATASOURCE_ID`.

---

## Outputs

Mutations to `SubgraphExecutionState`:

- `physical_validator_response` (`PhysicalValidatorResponse`)
- `errors` and `reasoning`

Side effects:

- Sandbox execution pool usage via `execute_in_sandbox`.
- Adapter calls to `dry_run()` and `cost_estimate()`.

---

## Internal Flow (Step-by-Step)

1. If SQL is missing, return empty response.
2. Resolve datasource adapter via registry.
3. `_validate_semantic()` runs dry‑run in sandbox under `DB_BREAKER`.
4. `_validate_performance()` runs cost estimate in sandbox under `DB_BREAKER`.
5. Collect errors/warnings and return `PhysicalValidatorResponse`.
6. On exceptions, emit `PHYSICAL_VALIDATOR_FAILED`.

---

## Contracts & Interfaces

Implements a LangGraph node callable:

```
def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]
```

Key contracts:

- `PhysicalValidatorResponse`
- `ExecutionRequest` / `ExecutionResult`

---

## Determinism Guarantees

- Deterministic for a fixed SQL and adapter behavior.
- External DB behavior can vary across runs.

---

## Error Handling

Emits `PipelineError` with:

- `MISSING_DATASOURCE_ID`
- `EXECUTION_ERROR`
- `EXECUTOR_CRASH`
- `PERFORMANCE_WARNING`
- `SERVICE_UNAVAILABLE`
- `PHYSICAL_VALIDATOR_FAILED`

---

## Retry + Idempotency

- No internal retry logic beyond circuit breaker behavior.
- Idempotency depends on adapter dry‑run semantics.

---

## Performance Characteristics

- Uses process pool execution (sandbox).
- Cost estimation and dry‑run are external DB calls.

---

## Observability

- Logger: `physical_validator`
- Uses `DB_BREAKER` which logs breaker state changes.

---

## Configuration

- None directly; uses sandbox pool sizing from settings and adapter config.

---

## Extension Points

- Wire into `build_sql_agent_graph()` between generator and executor.
- Extend dry‑run and cost estimation logic per adapter.

---

## Known Limitations

- Not connected to the default SQL agent graph.
- Behavior depends on adapter support for dry‑run and cost estimation.

---

## Related Code

- `packages/core/src/nl2sql/pipeline/nodes/validator/physical_node.py`
