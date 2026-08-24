"""
Query API for NL2SQL

Provides functionality for executing natural language queries against databases.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.auth import UserContext
from nl2sql.execution.contracts import ArtifactRef


class SubQueryResult(BaseModel):
    """Represents the result of a sub-query execution."""
    id: str = Field(default="")
    intent: str = Field(default="")
    sql: str = Field(default="")
    datasource_id: str = Field(default="")
    schema_version: str = Field(default="")


class QueryResult(BaseModel):
    """Represents the result of a query execution.

    Row data is deliberately not inlined: results live in artifact storage and are
    addressable through ``artifact_refs``.
    """
    sub_queries: List[SubQueryResult] = Field(default_factory=list)
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: str = Field(default="")
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_refs: Dict[str, ArtifactRef] = Field(default_factory=dict)


def _field(source: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a mapping or an object.

    LangGraph may hand back either dicts or model instances for nested state.
    """
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _enum_value(value: Any) -> str:
    """Render an enum (or plain value) as its string value."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _error_summary(error: Any) -> Dict[str, Any]:
    """Project a PipelineError onto a client-safe summary (no stack traces)."""
    return {
        "node": _field(error, "node", "") or "",
        "message": _field(error, "message", "") or "",
        "error_code": _enum_value(_field(error, "error_code")),
        "severity": _enum_value(_field(error, "severity")),
    }


def _sub_query_results(state: Dict[str, Any]) -> List[SubQueryResult]:
    """Build the per-sub-query view from ``subgraph_outputs``."""
    results: List[SubQueryResult] = []
    for output in (state.get("subgraph_outputs") or {}).values():
        sub_query = _field(output, "sub_query")
        if not sub_query:
            continue
        results.append(
            SubQueryResult(
                id=_field(sub_query, "id", "") or "",
                intent=_field(sub_query, "intent", "") or "",
                datasource_id=_field(sub_query, "datasource_id", "") or "",
                schema_version=_field(sub_query, "schema_version", "") or "",
                sql=_field(output, "sql_draft", "") or "",
            )
        )
    return results


def result_from_state(state: Dict[str, Any]) -> QueryResult:
    """Build a typed :class:`QueryResult` from a raw pipeline graph state."""
    state = state or {}
    return QueryResult(
        sub_queries=_sub_query_results(state),
        final_answer=_field(state.get("answer_synthesizer_response"), "final_answer"),
        errors=[_error_summary(error) for error in (state.get("errors") or [])],
        trace_id=state.get("trace_id") or "",
        reasoning=list(state.get("reasoning") or []),
        warnings=list(state.get("warnings") or []),
        artifact_refs=state.get("artifact_refs") or {},
    )


class QueryAPI:
    """
    API for executing natural language queries against databases.
    """

    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx

    def run_query(
        self,
        natural_language: str,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context: Optional[UserContext] = None,
    ) -> QueryResult:
        """
        Execute a natural language query against the database.

        Args:
            natural_language: The natural language query to execute
            datasource_id: Optional specific datasource to query (otherwise auto-resolved)
            execute: Whether to actually execute the SQL against the database
            user_context: Optional user context for permissions

        Returns:
            A :class:`QueryResult` built from the pipeline graph state.
        """
        state = run_with_graph(
            self._ctx,
            natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context
        )
        return result_from_state(state)
