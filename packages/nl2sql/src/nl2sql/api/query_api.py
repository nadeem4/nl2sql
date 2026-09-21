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
from nl2sql.common.errors import ErrorSeverity
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
from nl2sql.services.callbacks.token_handler import QuestionUsage

DEFAULT_SAMPLE_ROWS = 50


class RowSample(BaseModel):
    """A bounded window onto a sub-query's result set."""
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    total_rows: int = 0


class SubQueryResult(BaseModel):
    """Represents the result of a sub-query execution."""
    id: str = Field(default="")
    intent: str = Field(default="")
    sql: str = Field(default="")
    datasource_id: str = Field(default="")
    schema_version: str = Field(default="")
    plan: Optional[Dict[str, Any]] = None
    validation: List[ValidationCheck] = Field(default_factory=list)
    rows: Optional[RowSample] = None
    status: str = Field(default="")
    retry_count: int = Field(default=0)


class QueryResult(BaseModel):
    """Represents the result of a query execution.

    Carries everything a UI renders: the plan, the validation checks, a capped
    row sample, the SQL, per-node timings and the question's LLM token usage.
    The full result set still lives in artifact storage, addressable through
    ``artifact_refs``. ``trace_path`` is where this run's trace file was
    written, or None when ``TRACE_MODE`` did not write one.
    """
    sub_queries: List[SubQueryResult] = Field(default_factory=list)
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: str = Field(default="")
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_refs: Dict[str, ArtifactRef] = Field(default_factory=dict)
    status: str = Field(default="")
    timings: Dict[str, float] = Field(default_factory=dict)
    usage: QuestionUsage = Field(default_factory=QuestionUsage)
    trace_path: Optional[str] = None


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


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    """Render a nested model (or an already-plain dict) as a JSON-ready dict."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        return dump(mode="json")
    return None


def _row_sample(frame: Any, sample_rows: int) -> RowSample:
    """Cap a result frame at ``sample_rows`` while keeping the true total."""
    rows = list(_field(frame, "rows", []) or [])
    return RowSample(
        columns=list(_field(frame, "columns", []) or []),
        rows=[list(row) for row in rows[:sample_rows]],
        total_rows=int(_field(frame, "row_count", len(rows)) or 0),
    )


def _sub_query_results(
    state: Dict[str, Any],
    artifact_store: Any = None,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    warnings: Optional[List[Dict[str, Any]]] = None,
) -> List[SubQueryResult]:
    """Build the per-sub-query view from ``subgraph_outputs``."""
    results: List[SubQueryResult] = []
    for output in (state.get("subgraph_outputs") or {}).values():
        sub_query = _field(output, "sub_query")
        if not sub_query:
            continue

        artifact = _field(output, "artifact")
        rows: Optional[RowSample] = None
        if artifact_store is not None and artifact is not None:
            try:
                rows = _row_sample(artifact_store.read_result_frame(artifact), sample_rows)
            except Exception as exc:  # the result is still useful without its rows
                if warnings is not None:
                    warnings.append(
                        {
                            "node": "query_api",
                            "message": f"Could not read result rows for sub-query "
                            f"'{_field(sub_query, 'id', '') or ''}': {exc}",
                        }
                    )

        results.append(
            SubQueryResult(
                id=_field(sub_query, "id", "") or "",
                intent=_field(sub_query, "intent", "") or "",
                datasource_id=_field(sub_query, "datasource_id", "") or "",
                schema_version=_field(sub_query, "schema_version", "") or "",
                sql=_field(output, "sql_draft", "") or "",
                plan=_as_dict(_field(output, "plan")),
                validation=[
                    check
                    if isinstance(check, ValidationCheck)
                    else ValidationCheck.model_validate(check)
                    for check in (_field(output, "validation", []) or [])
                ],
                rows=rows,
                status=_field(output, "status", "") or "",
                retry_count=int(_field(output, "retry_count", 0) or 0),
            )
        )
    return results


def _overall_status(
    sub_queries: List[SubQueryResult],
    blocking_errors: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> str:
    """Summarise the run for a caller that renders one badge."""
    if blocking_errors:
        return "error"
    if not sub_queries:
        # Nothing ran, so neither "plan_only" nor "success" would be true.
        return ""
    outputs = list((state.get("subgraph_outputs") or {}).values())
    produced_rows = any(sub_query.rows is not None for sub_query in sub_queries) or any(
        _field(output, "artifact") is not None for output in outputs
    )
    return "success" if produced_rows else "plan_only"


def result_from_state(
    state: Dict[str, Any],
    artifact_store: Any = None,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
) -> QueryResult:
    """Build a typed :class:`QueryResult` from a raw pipeline graph state."""
    state = state or {}

    warnings: List[Dict[str, Any]] = list(state.get("warnings") or [])
    errors: List[Dict[str, Any]] = []
    for error in state.get("errors") or []:
        summary = _error_summary(error)
        if summary["severity"] == ErrorSeverity.WARNING.value:
            warnings.append(summary)
        else:
            errors.append(summary)

    sub_queries = _sub_query_results(state, artifact_store, sample_rows, warnings)

    return QueryResult(
        sub_queries=sub_queries,
        final_answer=_field(state.get("answer_synthesizer_response"), "final_answer"),
        errors=errors,
        trace_id=state.get("trace_id") or "",
        reasoning=list(state.get("reasoning") or []),
        warnings=warnings,
        artifact_refs=state.get("artifact_refs") or {},
        status=_overall_status(sub_queries, errors, state),
        timings=dict(state.get("timings") or {}),
        usage=QuestionUsage.model_validate(state.get("usage") or {}),
        trace_path=state.get("trace_path"),
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
        return result_from_state(
            state,
            artifact_store=getattr(self._ctx, "artifact_store", None),
        )
