from fastapi import APIRouter, HTTPException, Depends
from typing import Annotated
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql_api.dependencies import get_query_service
from nl2sql_api.services import QueryService

router = APIRouter()

QuerySvc = Annotated[QueryService, Depends(get_query_service)]

@router.post("/query", response_model=QueryResponse)
async def execute_query(
    payload: QueryRequest,
    service: QuerySvc,
):
    try:
        return service.execute_query(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/query/{trace_id}", response_model=QueryResponse)
async def get_query_result(
    trace_id: str,
    service: QuerySvc,
):
    return service.get_result(trace_id)
