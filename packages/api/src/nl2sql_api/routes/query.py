"""The `query` namespace of `/api/v1`: ask, stream, read results and traces, rate and correct.

Every route calls the facade as the request's caller and nothing else: a run, result or trace another caller asked is
a 404, and `feedback/correct` never acts as the trusted in-process operator.
"""
import logging
import sys
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from nl2sql import (NL2SQL, CancellationToken, CorrectionRefused, Event, LLMOverride, Proposal, QueryResult, ResultManifest,
                    ResultNotFound, ResultPage, UserContext)
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.models.query import CorrectionRequest, FeedbackRequest, QueryRequest
from nl2sql_api.overrides import llm_override, masked
from nl2sql_api.sse import event_stream

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]
Override = Annotated[Optional[LLMOverride], Depends(llm_override)]

_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such run or result for this caller."},
              **identity.AUTH_RESPONSES}
_KEYS = {403: {**ERROR_RESPONSE, "description": "The request carries keys or models and `api.request_llm` is off."}}


def _ask(payload: QueryRequest, engine: NL2SQL, user_context: UserContext, override: Optional[LLMOverride],
         **extra: Any) -> Dict[str, Any]:
    """The facade call a question makes; a choice and approvals together are refused before the engine is called."""
    if payload.choice is not None and payload.approval_ids:
        raise RequestRefused(422, "CLARIFICATION_INVALID", "A request answers a clarification or retries after an "
                             "approval, not both.", fix="Send either `choice` or `approval_ids`.")
    return dict(datasource_id=payload.datasource_id, execute=payload.execute, user_context=user_context,
                choice=payload.choice, approvals=payload.approval_ids or None, llm_override=override, **extra)


# Synchronous on purpose: the pipeline performs blocking LLM and database calls,
# so Starlette runs this handler in its threadpool instead of on the event loop.
@router.post("/query", response_model=QueryResult, summary="Ask a question",
             responses={**identity.AUTH_RESPONSES, **_KEYS})
def execute_query(payload: QueryRequest, engine: Engine, user_context: Caller, override: Override):
    """Run a natural-language question through the pipeline.

    Returns, per sub-query, the plan, the validation checks, the SQL and a capped row sample, plus the written
    answer, errors, timings and token usage. A pipeline failure (a refusal included) comes back with status 200 and
    `status: "error"`. `execute: false` plans and validates without running SQL. `choice` answers an earlier
    `clarification_request`; `approval_ids` names approvals an approver granted. The caller (user, tenant and roles)
    comes from the source `api.auth` names, never from the body; no identity is a 401. Where `api.request_llm` is on,
    a request may carry its own provider keys and models in headers.
    """
    return engine.run_query(payload.question, **_ask(payload, engine, user_context, override))


def _stream_failed() -> Event:
    return Event(name="run.failed", at=datetime.now(timezone.utc),
                 data={"message": "Failed to execute query.", "error_code": "UNKNOWN_ERROR"})


@router.post(
    "/query/stream",
    summary="Ask a question, streamed",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}},
              "description": "Server-Sent Events, the engine's run events: `run.started`, "
                             "`node.started`, `node.finished`, `result.manifest`, `answer.delta`, "
                             "`sanity.flagged`, `run.limit_reached`, then one `run.finished` (its "
                             "`data.result` is the `/query` response) or `run.failed`."},
        **identity.AUTH_RESPONSES, **_KEYS,
    },
)
def stream_query(payload: QueryRequest, request: Request, engine: Engine, user_context: Caller, override: Override):
    """The same run as `POST /query`, reported as it happens, as Server-Sent Events.

    Each SSE `event` is the event's name and its `data` line the whole event as
    JSON (`name`, `at`, `trace_id`, `tenant_id`, `data`): `run.started`;
    `node.started` (`node`, `sub_query_id`) and `node.finished` (plus `seconds`,
    `ok`) per pipeline step; `result.manifest` (`results`) as soon as the results
    are saved, so a client can start paging them with the event's `trace_id`;
    `answer.delta` (`field`: `summary` or `content`, `text`) as the answer model
    writes; `sanity.flagged` and `run.limit_reached`; then exactly one of
    `run.finished`, the run's summary whose `data.result` is the `/query`
    response, or `run.failed` (`message`, `error_code`) when the run could not
    finish. A refusal or a provider error is a `run.finished` with
    `status: "error"`, as `/query` returns it. Same body, headers and auth as `/query`; a request the engine refuses
    outright (a bad choice, a key it may not take) is the error envelope, before any event. A comment line keeps the
    connection open while nothing happens; closing the connection cancels the run before its next step.
    """
    token = CancellationToken()
    events = engine.stream_query(payload.question, **_ask(payload, engine, user_context, override,
                                                          cancellation_token=token))

    def guarded():
        try:
            yield from events
        except Exception:
            error = sys.exc_info()[1]
            logger.error("Unexpected failure while streaming a query\n%s", masked(request.headers, error))
            yield _stream_failed()

    return event_stream(guarded, request, token=token)


@router.get("/results/{trace_id}", response_model=ResultManifest, summary="List a run's results",
            responses=_NOT_FOUND)
def get_result_manifest(trace_id: str, engine: Engine, user_context: Caller):
    """Which results the run saved: per result its id, columns, row count and whether it was capped, never the rows.
    `trace_id` is the run's `trace_id`. A run is readable only by the caller who asked it (the same user id and
    tenant): an unknown run, another user's or another tenant's, or an id that is not a plain token is a 404."""
    try:
        return engine.results.manifest(trace_id, user_context=user_context)
    except ResultNotFound:
        raise RequestRefused(404, "NOT_FOUND", "No such result.") from None


# Declared before the page route: `{result_id}` would otherwise match `x.csv`.
@router.get(
    "/results/{trace_id}/{result_id}.csv",
    summary="Download a result as CSV",
    response_class=StreamingResponse,
    responses={**_NOT_FOUND, 200: {"content": {"text/csv": {}}, "description": "The whole result, header first."}},
)
def export_result_csv(trace_id: str, result_id: str, engine: Engine, user_context: Caller):
    """The whole of one saved result as CSV, streamed a slice at a time from its parquet file, never built in memory.
    `result_id` is a `node_id` from the run's manifest. Another caller's run, or an id that is not a plain token, is
    a 404."""
    try:
        chunks = engine.results.csv(trace_id, result_id, user_context=user_context)
    except ResultNotFound:
        raise RequestRefused(404, "NOT_FOUND", "No such result.") from None
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{trace_id}_{result_id}.csv"'},
    )


@router.get("/results/{trace_id}/{result_id}/chart", summary="Chart a result", responses={
    **_NOT_FOUND, 422: {**ERROR_RESPONSE, "description": "A kind or format the data cannot be drawn as."}})
def get_result_chart(
    trace_id: str,
    result_id: str,
    engine: Engine,
    user_context: Caller,
    kind: Annotated[Optional[Literal["line", "bar", "big_number", "table"]], Query(
        description="Force a kind the data can draw; by default five rules choose from its columns.")] = None,
    format: Annotated[Literal["spec", "vega-lite"], Query(
        description="`spec` is the engine's chart spec; `vega-lite` is a Vega-Lite v5 document, rows inline.")] = "spec",
):
    """The chart a saved result is drawn as, chosen from its column kinds with no model call. `format=vega-lite`
    returns a Vega-Lite v5 spec ready to render; a table has none, so asking for it is a 422."""
    try:
        spec = engine.results.chart(trace_id, result_id, user_context=user_context, kind=kind)
    except ResultNotFound:
        raise RequestRefused(404, "NOT_FOUND", "No such result.") from None
    if format == "spec":
        return spec
    vega = spec.to_vega_lite()
    if vega is None:
        raise RequestRefused(422, "INVALID_REQUEST", "A table has no Vega-Lite form.",
                             fix="Ask for format=spec, or force a kind the data can draw.")
    return vega


@router.get(
    "/results/{trace_id}/{result_id}",
    response_model=ResultPage,
    summary="Read a page of a result",
    responses=_NOT_FOUND,
)
def get_result_page(
    trace_id: str,
    result_id: str,
    engine: Engine,
    user_context: Caller,
    offset: Annotated[int, Query(ge=0, description="The first row to return, from 0.")] = 0,
    limit: Annotated[int, Query(ge=1, description="Rows per page; capped at 1,000.")] = 100,
):
    """Rows `offset` to `offset + limit` of one saved result, read lazily from its parquet file. `result_id` is a
    `node_id` from the run's manifest. `next_offset` is where the next page starts, null at the end; a page past the
    end is empty. Another caller's run, or an id that is not a plain token, is a 404."""
    try:
        return engine.results.page(trace_id, result_id, offset=offset, limit=limit, user_context=user_context)
    except ResultNotFound:
        raise RequestRefused(404, "NOT_FOUND", "No such result.") from None


@router.get("/traces/{trace_id}", summary="Read a run's trace", responses=_NOT_FOUND)
def get_trace(trace_id: str, engine: Engine, user_context: Caller) -> Dict[str, Any]:
    """The trace document of a run the caller asked: its request, every node's input and output, and the result. An
    unknown trace, another caller's, or an id that is not a plain token is a 404."""
    try:
        trace = engine.traces.get(trace_id, user_context=user_context)
    except ValueError:
        trace = None
    if trace is None:
        raise RequestRefused(404, "NOT_FOUND", "No such trace.")
    return trace


@router.post("/feedback", summary="Rate an answer", responses=_NOT_FOUND)
def rate(payload: FeedbackRequest, engine: Engine, user_context: Caller) -> Dict[str, Any]:
    """Record a thumbs up or down for a run the caller asked. The question, SQL and answer come from the run's
    trace, never from the body. Another caller's run is a 404."""
    return engine.feedback.record(payload.trace_id, payload.rating, note=payload.note, user_context=user_context)


@router.post("/feedback/correct", response_model=Proposal, summary="Correct an answer", responses={
    **_NOT_FOUND, 422: {**ERROR_RESPONSE, "description": "The correction was refused; `details` say why."}})
def correct(payload: CorrectionRequest, engine: Engine, user_context: Caller):
    """Turn the caller's fix (`sql` or `plan`, exactly one) for a run they asked into a proposal for a verified
    query. Nothing is saved: a reviewer accepts the proposal elsewhere. The correction is checked as the caller, so it
    is refused (422 `CORRECTION_REFUSED`) when it is not read-only, reads what the caller may not, or a hook denies it."""
    try:
        return engine.feedback.correct(payload.trace_id, sql=payload.sql, plan=payload.plan, question=payload.question,
                                       note=payload.note, user_context=user_context)
    except CorrectionRefused as error:
        if error.code == "no_run":
            raise RequestRefused(404, "NOT_FOUND", "No such run.") from None
        raise RequestRefused(422, "CORRECTION_REFUSED", str(error),
                             details=[{"code": error.code, "msg": message} for message in error.messages]) from None


@router.get("/feedback", summary="List the ratings", responses=identity.ADMIN_RESPONSES)
def list_feedback(engine: Engine, user_context: Annotated[UserContext, Depends(identity.admin)],
                  limit: Annotated[Optional[int], Query(ge=1, description="At most this many, newest first.")] = None
                  ) -> List[Dict[str, Any]]:
    """Every rating, newest first. Admin only: it spans all callers."""
    return engine.feedback.list(limit=limit)
