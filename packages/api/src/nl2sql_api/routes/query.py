import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from typing import Annotated
from nl2sql import CancellationToken, Event, ResultNotFound, ResultPage, UserContext
from nl2sql_api import auth
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql_api.dependencies import get_query_service
from nl2sql_api.services import QueryService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])

QuerySvc = Annotated[QueryService, Depends(get_query_service)]
Caller = Annotated[UserContext, Depends(auth.get_user_context)]


# Synchronous on purpose: the pipeline performs blocking LLM and database calls,
# so Starlette runs this handler in its threadpool instead of on the event loop.
@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Ask a question",
    responses={401: {"description": "No role for this request (see the auth settings)."}},
)
def execute_query(
    payload: QueryRequest,
    service: QuerySvc,
    user_context: Caller,
):
    """Run a natural-language question through the pipeline.

    Returns, per sub-query, the plan, the validation checks, the SQL and a capped row
    sample, plus the written answer, errors, timings and token usage. A pipeline
    failure (a refusal included) comes back with status 200 and `status: "error"`.
    `execute: false` plans and validates without running SQL. The RBAC role comes
    from a trusted proxy header (`NL2SQL_API_ROLE_HEADER`) or a static role
    (`NL2SQL_API_ROLE`); the body's `user_context` counts only with the dev flag
    `NL2SQL_API_TRUST_BODY_ROLE=true`. No role is a 401.
    """
    try:
        return service.execute_query(payload, user_context)
    except Exception:
        # Pipeline failures are reported in QueryResponse.errors with a 200; reaching
        # here means something genuinely unexpected broke.
        logger.exception("Unexpected failure while executing query")
        raise HTTPException(status_code=500, detail="Failed to execute query.")


# Proxies (nginx, a Space's) buffer a response unless told not to, which would
# hold every event back until the run ends.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}



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
        401: {"description": "No role for this request (see the auth settings)."},
    },
)
def stream_query(payload: QueryRequest, service: QuerySvc, user_context: Caller):
    """The same run as `POST /query`, reported as it happens, as Server-Sent Events.

    Each SSE `event` is the event's name and its `data` line the whole event as
    JSON (`name`, `at`, `trace_id`, `tenant_id`, `data`): `run.started`;
    `node.started` (`node`, `sub_query_id`) and `node.finished` (plus `seconds`,
    `ok`) per pipeline step; `result.manifest` (`results`) as soon as the results
    are saved, so a client can start paging them with the event's `trace_id`;
    `answer.delta` (`field`: `summary` or `content`, `text`) as the answer model
    writes; `sanity.flagged` and `run.limit_reached`; then exactly one of
    `run.finished`, the run's summary whose `data.result` is the `/query`
    response, or `run.failed` (`message`, `error_code`, and the engine's `user_id` and `roles`) when the run could not
    finish. A refusal or a provider error is a `run.finished` with
    `status: "error"`, as `/query` returns it. Same body and auth as `/query`;
    closing the connection cancels the run before its next step.
    """
    token = CancellationToken()

    def events():
        try:
            for event in service.stream_query(payload, user_context, token):
                yield event.sse()
        except Exception:
            logger.exception("Unexpected failure while streaming a query")
            yield _stream_failed().sse()

    async def body():
        try:
            async for chunk in iterate_in_threadpool(events()):
                yield chunk
        finally:
            # The client went away, or the run is over: stop it before its next step.
            token.cancel()

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)


_NOT_FOUND = {404: {"description": "No such run or result for this tenant."},
              401: {"description": "No role for this request (see the auth settings)."}}


# Declared before the page route: `{result_id}` would otherwise match `x.csv`.
@router.get(
    "/query/{request_id}/results/{result_id}.csv",
    summary="Download a result as CSV",
    response_class=StreamingResponse,
    responses={**_NOT_FOUND, 200: {"content": {"text/csv": {}}, "description": "The whole result, header first."}},
)
def export_result_csv(request_id: str, result_id: str, service: QuerySvc, user_context: Caller):
    """The whole of one saved result as CSV, streamed a slice at a time from its
    parquet file, never built in memory. `request_id` is the run's `trace_id`;
    `result_id` is a `node_id` from the query response's `result_manifest`. Results are
    read from the engine's own tenant folder only: an unknown run, another
    tenant's, or an id that is not a plain token is a 404."""
    try:
        chunks = service.export_result_csv(request_id, result_id)
    except ResultNotFound:
        raise HTTPException(status_code=404, detail="No such result.")
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{request_id}_{result_id}.csv"'},
    )


@router.get(
    "/query/{request_id}/results/{result_id}",
    response_model=ResultPage,
    summary="Read a page of a result",
    responses=_NOT_FOUND,
)
def get_result_page(
    request_id: str,
    result_id: str,
    service: QuerySvc,
    user_context: Caller,
    offset: Annotated[int, Query(ge=0, description="The first row to return, from 0.")] = 0,
    limit: Annotated[int, Query(ge=1, description="Rows per page; capped at 1,000.")] = 100,
):
    """Rows `offset` to `offset + limit` of one saved result, read lazily from its
    parquet file. `request_id` is the run's `trace_id`; `result_id` is a `node_id`
    from the query response's `result_manifest`. `next_offset` is where the next page
    starts, null at the end; a page past the end is empty. Results are read from
    the engine's own tenant folder only: an unknown run, another tenant's, or an
    id that is not a plain token is a 404."""
    try:
        return service.get_result_page(request_id, result_id, offset=offset, limit=limit)
    except ResultNotFound:
        raise HTTPException(status_code=404, detail="No such result.")
