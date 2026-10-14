"""
Query API for NL2SQL

Provides functionality for executing natural language queries against databases.
"""

from __future__ import annotations

import contextvars
import queue
import threading
from typing import Optional, List, Dict, Any, Iterator, Literal

from pydantic import BaseModel, Field
from nl2sql.runtime import Runtime
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.auth import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import ErrorSeverity
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.events import QueryEvent, RunEvents
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
    # "cache" when the plan came from the plan cache (no planner LLM call; it
    # was still validated, generated and executed), "verified" when there was
    # no plan because a verified query's SQL ran, else "llm".
    plan_source: str = Field(default="llm")
    # The row ceiling that bounded this sub-query's SQL, when the plan asked for
    # no smaller LIMIT; ``truncated`` is True when the result reached it, so
    # rows may be missing.
    row_cap: Optional[int] = None
    truncated: bool = False


class AnswerSource(BaseModel):
    """Where an answer came from.

    ``{"type": "verified", "id", "datasource_id", "params"}`` when a verified
    query from ``curated.yaml`` answered, with the values it ran with;
    ``{"type": "generated"}`` (the other fields None) when the pipeline
    planned and generated the SQL.
    """
    type: Literal["verified", "generated"] = "generated"
    id: Optional[str] = None
    datasource_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class QueryResult(BaseModel):
    """Represents the result of a query execution.

    Carries everything a UI renders: the plan, the validation checks, a capped
    row sample, the SQL, per-node timings and the question's LLM token usage.
    The full result set still lives in artifact storage, addressable through
    ``artifact_refs``. ``result_manifest`` lists the run's saved results (its
    manifest: node id, columns, row count, truncated), never their rows; read
    those by ``trace_id`` with ``engine.results.page`` or ``engine.results.csv``.
    ``trace_path`` is where this run's trace file was written, or None when
    ``TRACE_MODE`` did not write one.
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
    # True when any sub-query's result was cut at its row ceiling.
    truncated: bool = False
    # The run's saved results, one per terminal DAG node: the manifest, not the rows.
    result_manifest: List[ResultSummary] = Field(default_factory=list)
    # A verified query's answer, or a generated one.
    source: AnswerSource = Field(default_factory=AnswerSource)


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
        # Set for a PROVIDER_* error: who refused the call, and in their words.
        "provider": _field(error, "provider"),
        "provider_response": _field(error, "provider_response"),
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
                plan_source=_field(output, "plan_source", "llm") or "llm",
                row_cap=_field(output, "row_cap"),
                truncated=bool(_field(output, "truncated", False)),
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
    usage = QuestionUsage.model_validate(state.get("usage") or {})
    usage.plan_cache_hits = sum(1 for sq in sub_queries if sq.plan_source == "cache")

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
        usage=usage,
        trace_path=state.get("trace_path"),
        truncated=any(sq.truncated for sq in sub_queries),
        result_manifest=list(_field(state.get("aggregator_response"), "results", None) or []),
        source=_source(state.get("verified_match")),
    )


def _source(match: Any) -> AnswerSource:
    """The run's provenance: the verified query that answered it, if one did."""
    if not match:
        return AnswerSource()
    return AnswerSource(type="verified", id=_field(match, "query_id"),
                        datasource_id=_field(match, "datasource_id"),
                        params=dict(_field(match, "params", None) or {}))


class QueryAPI:
    """Runs questions for ``NL2SQL.run_query`` and ``stream_query``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def run_query(
        self,
        question: str,
        *,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context: Optional[UserContext] = None,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> QueryResult:
        """
        Execute a natural language query against the database.

        Args:
            question: The question, in plain language
            datasource_id: Optional specific datasource to query (otherwise auto-resolved)
            execute: Whether to actually execute the SQL against the database
            user_context: Optional user context for permissions
            cancellation_token: Optional token the caller cancels to stop the
                run. It stops before the next step or model call; the one in
                flight finishes, and the result carries a ``CANCELLED`` error.

        Returns:
            A :class:`QueryResult` built from the pipeline graph state.
        """
        state = run_with_graph(
            self._ctx,
            question,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context,
            cancellation_token=cancellation_token,
        )
        return result_from_state(
            state,
            artifact_store=getattr(self._ctx, "artifact_store", None),
        )

    def stream_query(
        self,
        question: str,
        *,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context: Optional[UserContext] = None,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> Iterator[QueryEvent]:
        """
        Run a question as :meth:`run_query` does, yielding the run as it happens.

        Yields ``node_started`` and ``node_finished`` per pipeline step,
        ``result_manifest`` once the aggregator has saved the results,
        ``answer_delta`` per piece of the answer as the model writes it, and
        then exactly one of ``final`` (the :class:`QueryResult`
        ``run_query`` would have returned, as JSON) or ``error`` (the run
        could not start, or crashed outside the pipeline). A run that fails
        inside the pipeline -- a refusal, a provider error -- ends in
        ``final`` with ``status: "error"``, as ``run_query`` returns it. See
        :mod:`nl2sql.pipeline.events`.

        The run goes on in a thread of its own; the caller's context (a
        request's keys) goes with it. Closing the iterator early -- a client
        that went away -- cancels the run before its next step or model call.
        """
        token = cancellation_token or CancellationToken()
        events: "queue.Queue[Optional[QueryEvent]]" = queue.Queue()

        def run() -> None:
            try:
                state = run_with_graph(
                    self._ctx,
                    question,
                    datasource_id=datasource_id,
                    execute=execute,
                    callbacks=[RunEvents(events.put)],
                    user_context=user_context,
                    cancellation_token=token,
                    stream_answer=True,
                )
                result = result_from_state(state, artifact_store=getattr(self._ctx, "artifact_store", None))
                events.put(QueryEvent(type="final", data=result.model_dump(mode="json")))
            except Exception as exc:
                events.put(QueryEvent(type="error", data={"message": f"The run failed: {exc}",
                                                          "error_code": "UNKNOWN_ERROR"}))
            finally:
                events.put(None)

        worker = threading.Thread(target=contextvars.copy_context().run, args=(run,),
                                  name="nl2sql-stream", daemon=True)
        worker.start()
        finished = False
        try:
            while (event := events.get()) is not None:
                yield event
                # A timed-out run's graph winds down in the background; whatever
                # it reports after the terminal event is not this run's any more.
                if event.type in ("final", "error"):
                    finished = True
                    return
        finally:
            if not finished:
                token.cancel()
