# Architecture Audit Remediation Plan

This document serves as the backlog for addressing findings from the Architectural Audit (2026-01-16).

## 🔴 Critical & High Priority Bugs

- [x] **BUG-010: Topology Naming Mismatch** (Critical)
  - **Component**: Core / Graph
  - **Issue**: The `Graph` defines the execution node as `"sql_agent_subgraph"`, but edges and `Send` payloads reference it as `"sql_agent"`. This causes runtime routing failures.
  - **Fix**: Rename the node registration in `graph.py` to match the references (`"sql_agent"`).
  - **Status**: Fixed.

- [x] **BUG-011: LLM Fallback in Aggregation** (Critical)
  - **Component**: Core / Aggregation
  - **Issue**: `AggregatorNode` falls back to "LLM Aggregation" if no `ResultPlan` is present. This violates the strict architectural requirement that no LLM runs after SQL execution.
  - **Fix**: Remove `_display_result_with_llm` and all fallback logic. strictly require `ResultPlan`.
  - **Status**: Fixed.

- [x] **BUG-012: Optional Planning Optimization** (High)
  - **Component**: Core / Global Planner
  - **Issue**: `GlobalPlannerNode` skips planning if `len(sub_queries) <= 1`. This forces the Aggregator into the unsafe "LLM Fallback" path for simple queries.
  - **Fix**: Remove the optimization. Always generate a `ResultPlan`, even for single-table projections.
  - **Status**: Fixed.

- [x] **BUG-013: Silent Partial Failures** (High)
  - **Component**: Core / Aggregator
  - **Issue**: `_execute_deterministic_plan` treats missing/failed subquery results as empty DataFrames (0 rows) without alerting.
  - **Fix**: Implement strict dependency checking. Raise `PipelineError` if a required SubQuery result is missing or has errors.
  - **Status**: Fixed.

## 🚀 Enhancements

- [x] **ENH-008: Strict Schema Verification** (Architecture)
  - **Value**: Ensures only typed `Expr` objects are used in the pipeline.
  - **Action**: Add runtime validation in `AggregatorNode` to ensure no raw strings are passed to DuckDB.
  - **Status**: Completed.
