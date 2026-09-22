import logging

from fastapi import APIRouter, HTTPException, Depends
from typing import Annotated
from nl2sql import UserContext
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
