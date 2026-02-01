from fastapi import APIRouter, HTTPException
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql_api.services.nl2sql_service import NL2SQLService

router = APIRouter()

def get_service():
    return NL2SQLService()

@router.post("/query", response_model=QueryResponse)
async def execute_query(request: QueryRequest):
    try:
        service = get_service()
        return service.execute_query(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/query/{trace_id}", response_model=QueryResponse)
async def get_query_result(trace_id: str):
    # TODO: Implement query result retrieval by trace_id
    # This would typically retrieve cached results
    raise HTTPException(status_code=404, detail="Query result not found")