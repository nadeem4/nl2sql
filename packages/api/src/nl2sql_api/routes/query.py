import logging

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from typing import Annotated
from nl2sql import ResultNotFound, ResultPage, UserContext
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
