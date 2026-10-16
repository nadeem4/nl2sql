"""
Query API for NL2SQL

Provides functionality for executing natural language queries against databases.
"""

from __future__ import annotations

import contextvars
import queue
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Iterable, Iterator, Literal, Mapping, Sequence

from pydantic import BaseModel, Field
from nl2sql.runtime import Runtime
from nl2sql.pipeline.runtime import run_with_graph, usage_prices
from nl2sql.policies import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import ErrorCode, ErrorSeverity, RunWarning
from nl2sql.events import STREAM_EVENTS, RunScope, RunStatus
from nl2sql_sdk.events import Event
from nl2sql.hooks import hooks_of
from nl2sql.hooks.approval import Approval, ApprovalRequest
from nl2sql.limits import Budget, RunStop
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.events import RunEvents
from nl2sql.pipeline.pinned import PinnedRun, SuppliedPlan, check_pinned
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
from nl2sql.services.callbacks.token_handler import QuestionUsage, TokenUsageCallback
from nl2sql.clarify.models import ClarificationRequest
from nl2sql.explain.models import Explanation
from nl2sql.knowledge.models import KnowledgeUsed
from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.models import Estimate

DEFAULT_SAMPLE_ROWS = 50
# A run's stream ends with exactly one of these.
TERMINAL_EVENTS = frozenset({"run.finished", "run.failed"})


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
    # no plan because a verified query's SQL ran, "supplied" for a plan passed
    # to run_query(plan=...), "approved" for an approval's pinned plan
    # ("chosen" is Wave B's), else "llm".
    plan_source: Literal["llm", "cache", "verified", "supplied", "approved", "chosen"] = "llm"
    # The row ceiling that bounded this sub-query's SQL, when the plan asked for
    # no smaller LIMIT; ``truncated`` is True when the result reached it, so
    # rows may be missing.
    row_cap: Optional[int] = None
    truncated: bool = False
    # The knowledge this sub-query's prompts carried (glossary terms and rules), and the database's
    # estimate of the rows it reads, when the cost rule asked for one. knowledge_used holds only items
    # within the caller's scope: nothing on, or mentioning, a table the caller cannot read (D39), so
    # returning it names no denied table.
    knowledge_used: Optional[KnowledgeUsed] = None
    estimate: Optional[Estimate] = None


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
    ``settings.traces.mode`` did not keep one or the trace store is not the
    ``files`` store (read it with ``engine.traces.get(trace_id)``).
    """
    sub_queries: List[SubQueryResult] = Field(default_factory=list)
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: str = Field(default="")
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    # What the answer should say without failing the run (D34): a missing filter value, a join
    # fan-out, an all-null column, a cut or unconverted result, an earlier attempt a retry replaced.
    warnings: List[RunWarning] = Field(default_factory=list)
    artifact_refs: Dict[str, ArtifactRef] = Field(default_factory=dict)
    # How the run ended: success, plan_only, error, needs_approval (see
    # approvals_needed), stopped (see stop), or needs_clarification (reserved
    # for Wave B's clarification result; nothing produces it yet). None when
    # nothing ran and nothing failed.
    status: Optional[RunStatus] = None
    timings: Dict[str, float] = Field(default_factory=dict)
    usage: QuestionUsage = Field(default_factory=QuestionUsage)
    trace_path: Optional[str] = None
    # True when any sub-query's result was cut at its row ceiling.
    truncated: bool = False
    # The run's saved results, one per terminal DAG node: the manifest, not the rows.
    result_manifest: List[ResultSummary] = Field(default_factory=list)
    # A verified query's answer, or a generated one.
    source: AnswerSource = Field(default_factory=AnswerSource)
    # Why the run stopped at a limit (settings.pipeline.max_llm_calls or max_cost_usd), when status is "stopped".
    stop: Optional[RunStop] = None
    # What a person must approve before the question runs, one per pending sub-query, when status is
    # "needs_approval". Re-run the question with an Approval for each.
    approvals_needed: List[ApprovalRequest] = Field(default_factory=list)
    # The readings to choose from, when status is "needs_clarification" (from L1).
    clarification: Optional[ClarificationRequest] = None
    # The run's plan in plain English, rendered from the typed plans with no model call: one step per
    # sub-query with a plan or a verified answer; None when the run has neither.
    explanation: Optional[Explanation] = None


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
    warnings: Optional[List[RunWarning]] = None,
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
                    sub_query_id = _field(sub_query, "id", "") or ""
                    warnings.append(RunWarning(
                        code=ErrorCode.UNKNOWN_ERROR.value,
                        node="query_api",
                        message=f"Could not read result rows for sub-query '{sub_query_id}': {exc}",
                        sub_query_id=sub_query_id or None,
                    ))

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
                knowledge_used=_field(output, "knowledge_used"),
                estimate=_field(output, "estimate"),
            )
        )
    return results


def _overall_status(
    sub_queries: List[SubQueryResult],
    blocking_errors: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> Optional[str]:
    """Summarise the run for a caller that renders one badge.

    A run that asked for approval, or stopped at a limit, says so before its
    error does: the state's ``approvals_needed`` and ``stop`` are how the
    pipeline reports them.
    """
    if state.get("approvals_needed"):
        return "needs_approval"
    if state.get("stop"):
        return "stopped"
    if blocking_errors:
        return "error"
    if not sub_queries:
        # Nothing ran, so neither "plan_only" nor "success" would be true.
        return None
    outputs = list((state.get("subgraph_outputs") or {}).values())
    produced_rows = any(sub_query.rows is not None for sub_query in sub_queries) or any(
        _field(output, "artifact") is not None for output in outputs
    )
    return "success" if produced_rows else "plan_only"


# What a hook ends a run with; such a run's result carries none of its data.
_HOOK_ENDINGS = {ErrorCode.HOOK_DENIED.value, ErrorCode.HOOK_FAILED.value, ErrorCode.NEEDS_APPROVAL.value}


def result_from_state(
    state: Dict[str, Any],
    artifact_store: Any = None,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    knowledge: Optional[Mapping[str, DatasourceKnowledge]] = None,
) -> QueryResult:
    """Build a typed :class:`QueryResult` from a raw pipeline graph state.

    ``explanation`` is rendered here from the state's plans, with no model
    call (``nl2sql.explain``); ``knowledge`` maps a datasource id to its
    glossary, which names tables and columns and tells a glossary join from
    a declared key.
    """
    from nl2sql.explain.render import explain_state  # the renderer loads only when a run is assembled

    state = state or {}

    warnings: List[RunWarning] = [RunWarning.model_validate(w) for w in state.get("warnings") or []]
    errors: List[Dict[str, Any]] = []
    for error in state.get("errors") or []:
        summary = _error_summary(error)
        if summary["severity"] == ErrorSeverity.WARNING.value:
            warnings.append(RunWarning(code=summary["error_code"] or "", node=summary["node"],
                                       message=summary["message"]))
        else:
            errors.append(summary)

    # A hook that ended the run withholds its data as well as its answer:
    # no rows, no artifact references, no result manifest.
    withheld = any(error.get("error_code") in _HOOK_ENDINGS for error in errors)
    sub_queries = _sub_query_results(state, None if withheld else artifact_store, sample_rows, warnings)
    usage = QuestionUsage.model_validate(state.get("usage") or {})
    usage.plan_cache_hits = sum(1 for sq in sub_queries if sq.plan_source == "cache")

    return QueryResult(
        sub_queries=sub_queries,
        final_answer=_field(state.get("answer_synthesizer_response"), "final_answer"),
        errors=errors,
        trace_id=state.get("trace_id") or "",
        reasoning=list(state.get("reasoning") or []),
        warnings=warnings,
        artifact_refs={} if withheld else state.get("artifact_refs") or {},
        status=_overall_status(sub_queries, errors, state),
        timings=dict(state.get("timings") or {}),
        usage=usage,
        trace_path=state.get("trace_path"),
        truncated=any(sq.truncated for sq in sub_queries),
        result_manifest=[] if withheld else list(_field(state.get("aggregator_response"), "results", None) or []),
        source=_source(state.get("verified_match")),
        stop=state.get("stop"),
        approvals_needed=list(state.get("approvals_needed") or []),
        explanation=explain_state(state, warnings, knowledge),
    )


def tables_read(result: QueryResult, ds_registry: Any = None) -> List[str]:
    """The tables ``result``'s sub-queries read, as ``datasource.schema.table``, for the run's summary.

    From each sub-query's plan or, on the verified path, from its SQL parsed
    in the adapter's dialect. A table with no schema is in the adapter's
    default schema. A sub-query whose tables cannot be read is left out: a
    summary never fails a run over a table name.
    """
    from nl2sql.config.curated import inspect_verified_sql
    from nl2sql.policies.scope import default_schema

    tables = set()
    for sq in result.sub_queries:
        ds_id = sq.datasource_id
        if not ds_id:
            continue
        pairs: List[Any] = []
        if sq.plan:
            pairs = [(t.get("schema_name"), t.get("name")) for t in sq.plan.get("tables") or []]
        elif sq.sql and ds_registry is not None:
            try:
                pairs = list(inspect_verified_sql(sq.sql, ds_registry.get_dialect(ds_id)).tables)
            except Exception:
                pairs = []
        for schema, table in pairs:
            if table:
                tables.add(f"{ds_id}.{schema or default_schema(ds_registry, ds_id)}.{table}")
    return sorted(tables)


def _source(match: Any) -> AnswerSource:
    """The run's provenance: the verified query that answered it, if one did."""
    if not match:
        return AnswerSource()
    return AnswerSource(type="verified", id=_field(match, "query_id"),
                        datasource_id=_field(match, "datasource_id"),
                        params=dict(_field(match, "params", None) or {}))


def _pin(plan: Optional[SuppliedPlan], pinned: Optional[PinnedRun], approvals: Optional[Sequence[Approval]],
         datasource_id: Optional[str]) -> Optional[PinnedRun]:
    """The run's pin from ``plan`` or ``pinned``, checked before the run's scope opens (``check_pinned``)."""
    if plan is not None and pinned is not None:
        raise ValueError("Pass a plan or a pin, not both.")
    pin = PinnedRun.supplied(plan) if plan is not None else pinned
    check_pinned(pin, list(approvals or []), datasource_id)
    return pin


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
        scope: Optional[RunScope] = None,
        plan: Optional[SuppliedPlan] = None,
        approvals: Optional[Sequence[Approval]] = None,
        pinned: Optional[PinnedRun] = None,
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
            scope: A scope :meth:`open_scope` opened (Wave C's turn); the run
                goes on in it and closes it, instead of opening its own.
            plan: A plan the caller holds: the run skips the curated matcher,
                the resolver, the decomposer and the planner, and validates,
                generates and runs it under ``user_context``'s scope.
            approvals: Approvals for what an earlier run of the question asked
                (its ``approvals_needed``); the first that is this run's, by a
                holder of the hook's approver roles, pins its decomposition
                and plan.
            pinned: (internal) a pin of another source, such as Wave B's
                chosen plan, in place of ``plan``.

        Returns:
            A :class:`QueryResult` built from the pipeline graph state.

        Raises:
            ValueError: at the call, before anything runs: ``datasource_id``
                is not the plan's datasource, a chosen plan comes with
                approvals, or both ``plan`` and ``pinned`` are given.
        """
        pinned = _pin(plan, pinned, approvals, datasource_id)
        scope = scope or self.open_scope(question, datasource_id=datasource_id, execute=execute,
                                         user_context=user_context)
        # Entered here too, not only in the graph's worker, so every log line of the run carries its trace_id.
        with scope.entered():
            try:
                state = run_with_graph(
                    self._ctx,
                    question,
                    datasource_id=datasource_id,
                    execute=execute,
                    user_context=user_context,
                    cancellation_token=cancellation_token,
                    scope=scope,
                    pinned=pinned,
                    approvals=approvals,
                )
                result = result_from_state(
                    state,
                    artifact_store=getattr(self._ctx, "artifact_store", None),
                )
            except BaseException as exc:
                # A KeyboardInterrupt too: every scope ends with a terminal event.
                scope.fail(f"The run failed: {exc or type(exc).__name__}", ErrorCode.UNKNOWN_ERROR)
                raise
            self.close_scope(scope, result)
        return result

    def open_scope(self, question: str, *, datasource_id: Optional[str] = None, execute: bool = True,
                   user_context: Optional[UserContext] = None) -> RunScope:
        """Open a run's scope on the engine's bus and publish ``run.started`` (wave_a.md, section 3).

        ``run_query`` and ``stream_query`` open one themselves. A caller that
        works in the run before the question runs (Wave C's rewrite) opens it
        here, makes its model calls with ``scope.callbacks()`` inside
        ``scope.entered()``, then passes it as ``scope=``, or closes it with
        :meth:`close_scope` and a result built without a graph run.

        The scope carries a :class:`~nl2sql.pipeline.events.RunEvents`, so the
        graph publishes the run's steps (``node.*``, ``result.manifest``,
        ``answer.delta``) through it, and the question's
        :class:`~nl2sql.limits.Budget` (``settings.pipeline.max_llm_calls`` and
        ``max_cost_usd``), so every model call of the run, in the graph or
        not, is checked against the caps first.
        """
        scope = RunScope(self._ctx.bus, question=question, user_context=user_context, datasource_id=datasource_id,
                         execute=execute, tenant_id=self._ctx.tenant_id,
                         usage=TokenUsageCallback(prices=usage_prices(self._ctx)))
        # While a hook may withhold the answer at answer.after, the manifest waits for it.
        hooks = hooks_of(self._ctx)
        scope.attach(RunEvents(scope.publish, hold_manifest=hooks.attached("answer.after")))
        budget = Budget.for_question(self._ctx.settings.pipeline, scope.usage)
        if budget is not None:
            scope.attach(budget)
        scope.start()
        return scope

    def close_scope(self, scope: RunScope, result: QueryResult) -> Optional[Event]:
        """Close ``scope`` with ``run.finished``, the result's summary; ``run.failed`` if that cannot be built.

        ``result`` may be one built without a graph run (Wave C's failed
        rewrite). A scope closes once; closing it again does nothing and
        returns None. Returns the ``run.finished`` event published.
        """
        try:
            return scope.finish(result, tables=tables_read(result, getattr(self._ctx, "ds_registry", None)))
        except Exception as exc:
            scope.fail(f"The run's summary could not be built: {exc}", ErrorCode.UNKNOWN_ERROR)
            raise

    def stream_query(
        self,
        question: str,
        *,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context: Optional[UserContext] = None,
        cancellation_token: Optional[CancellationToken] = None,
        scope: Optional[RunScope] = None,
        names: Iterable[str] = STREAM_EVENTS,
        plan: Optional[SuppliedPlan] = None,
        approvals: Optional[Sequence[Approval]] = None,
        pinned: Optional[PinnedRun] = None,
    ) -> Iterator[Event]:
        """
        Run a question as :meth:`run_query` does, yielding its events as they are published.

        Yields this run's bus events from the caller-safe allow-list (D17,
        ``nl2sql.events.STREAM_EVENTS``), in publish order: ``run.started``,
        ``node.started`` and ``node.finished`` per pipeline step,
        ``result.manifest`` once the aggregator has saved the results,
        ``answer.delta`` per piece of the answer as the model writes it,
        ``sanity.flagged`` and ``run.limit_reached``, and then exactly one of
        ``run.finished`` or ``run.failed``. The stream's ``run.finished`` is
        the bus event's summary plus ``data.result``, the :class:`QueryResult`
        ``run_query`` would have returned, as JSON; the event published on the
        bus never holds it (D15). ``run.failed`` means the run could not start,
        or crashed outside the pipeline. A run that fails inside the pipeline
        -- a refusal, a provider error -- ends in ``run.finished`` with
        ``status: "error"``, as ``run_query`` returns it.

        The run goes on in a thread of its own; the caller's context (a
        request's keys) goes with it. Closing the iterator early -- a client
        that went away -- cancels the run before its next step or model call.
        ``scope`` is as for :meth:`run_query`: the stream starts with what the
        open scope already published (its ``run.started``); a closed one is a
        ``ValueError``, raised at the call. Whatever breaks, the stream ends
        with exactly one terminal event. ``names``
        (internal) is the allow-list; Wave C's conversation stream adds its
        own events to it. ``plan``, ``approvals`` and ``pinned`` are as for
        :meth:`run_query`, and refused the same way, at the call.
        """
        if scope is not None and scope.closed:
            raise ValueError("This run's scope is already closed; a stream needs an open one.")
        pinned = _pin(plan, pinned, approvals, datasource_id)
        return self._stream(question, datasource_id=datasource_id, execute=execute, user_context=user_context,
                            token=cancellation_token or CancellationToken(), opened=scope, allowed=frozenset(names),
                            pinned=pinned, approvals=approvals)

    def _stream(self, question: str, *, datasource_id: Optional[str], execute: bool,
                user_context: Optional[UserContext], token: CancellationToken, opened: Optional[RunScope],
                allowed: frozenset, pinned: Optional[PinnedRun] = None,
                approvals: Optional[Sequence[Approval]] = None) -> Iterator[Event]:
        """:meth:`stream_query`'s run, on a thread of its own, and the events it hands over."""
        events: "queue.Queue[Optional[Event]]" = queue.Queue()
        terminal_sent = threading.Event()

        def deliver(event: Event) -> None:
            if event.name in TERMINAL_EVENTS:
                terminal_sent.set()
            events.put(event)

        def run() -> None:
            scope: Optional[RunScope] = opened
            try:
                scope = scope or self.open_scope(question, datasource_id=datasource_id, execute=execute,
                                                 user_context=user_context)
                # What the scope published before the stream joined it, then the rest as it comes. The
                # stream's own run.finished, with the result, is delivered below.
                scope.follow(deliver, allowed - {"run.finished"})
                with scope.entered():
                    state = run_with_graph(
                        self._ctx,
                        question,
                        datasource_id=datasource_id,
                        execute=execute,
                        user_context=user_context,
                        cancellation_token=token,
                        stream_answer=True,
                        scope=scope,
                        pinned=pinned,
                        approvals=approvals,
                    )
                    result = result_from_state(state, artifact_store=getattr(self._ctx, "artifact_store", None))
                    finished = self.close_scope(scope, result)
                if finished is not None and "run.finished" in allowed:
                    deliver(finished.model_copy(update={
                        "data": {**finished.data, "result": result.model_dump(mode="json")}}))
            except BaseException as exc:
                # A KeyboardInterrupt too: every scope, and every stream, ends with a terminal event.
                message = f"The run failed: {exc or type(exc).__name__}"
                if scope is not None:
                    scope.fail(message, ErrorCode.UNKNOWN_ERROR)  # reaches the stream while it is attached
                if not terminal_sent.is_set():
                    # The scope could not open, was already closed, or broke on its terminal event: the
                    # caller still hears how the run ended.
                    deliver(Event(name="run.failed", at=datetime.now(timezone.utc),
                                  trace_id=scope.trace_id if scope is not None else None,
                                  tenant_id=scope.tenant_id if scope is not None
                                  else getattr(self._ctx, "tenant_id", None),
                                  data={"message": message, "error_code": ErrorCode.UNKNOWN_ERROR.value}))
                if not isinstance(exc, Exception):
                    raise
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
                if event.name in TERMINAL_EVENTS:
                    finished = True
                    return
        finally:
            if not finished:
                token.cancel()
