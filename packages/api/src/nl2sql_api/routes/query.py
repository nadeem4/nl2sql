from fastapi import APIRouter, HTTPException, Request

from nl2sql_api.models.query import QueryRequest, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def execute_query(request: Request, payload: QueryRequest):
    try:
        service = request.app.state.container.query
        return service.execute_query(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/query/{trace_id}", response_model=QueryResponse)
async def get_query_result(request: Request, trace_id: str):
    service = request.app.state.container.query
    return service.get_result(trace_id)
