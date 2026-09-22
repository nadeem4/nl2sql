import logging

from fastapi import APIRouter, HTTPException, Depends
from typing import Annotated
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql_api.dependencies import get_query_service
from nl2sql_api.services import QueryService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])

QuerySvc = Annotated[QueryService, Depends(get_query_service)]


# Synchronous on purpose: the pipeline performs blocking LLM and database calls,
# so Starlette runs this handler in its threadpool instead of on the event loop.
@router.post("/query", response_model=QueryResponse, summary="Ask a question")
def execute_query(
    payload: QueryRequest,
    service: QuerySvc,
):
    """Run a natural-language question through the pipeline.

    Returns, per sub-query, the plan, the validation checks, the SQL and a capped row
    sample, plus the written answer, errors, timings and token usage. A pipeline
    failure (a refusal included) comes back with status 200 and `status: "error"`.
    `execute: false` plans and validates without running SQL. `user_context.roles`
    selects the RBAC role; there is no authentication.
    """
    try:
        return service.execute_query(payload)
    except Exception:
        # Pipeline failures are reported in QueryResponse.errors with a 200; reaching
        # here means something genuinely unexpected broke.
        logger.exception("Unexpected failure while executing query")
        raise HTTPException(status_code=500, detail="Failed to execute query.")
