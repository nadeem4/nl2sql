# ADR-002: Circuit Breakers for External Dependencies

## Status

Accepted (implemented in `resilience.py`).

## Context

LLM providers, vector stores, and databases can experience outages. Unbounded retries degrade system reliability.

## Decision

Use `pybreaker` circuit breakers for each dependency class:

- `LLM_BREAKER`
- `VECTOR_BREAKER`
- `DB_BREAKER`

All breakers are created with `create_breaker()` and emit log events via `ObservabilityListener`.

## Consequences

- Fail-fast behavior when dependencies are down.
- Retry loops in the SQL agent only apply to retryable errors, not open circuits.

```mermaid
flowchart TD
    Call[Dependency Call] --> Breaker[create_breaker()]
    Breaker -->|closed| Execute[Execute]
    Breaker -->|open| FailFast[Fail Fast]
```

## Source references

- `packages/core/src/nl2sql/common/resilience.py`
