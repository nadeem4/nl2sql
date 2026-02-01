from fastapi import APIRouter, HTTPException
from nl2sql_api.models.schema import SchemaResponse
from nl2sql_api.services.nl2sql_service import NL2SQLService

router = APIRouter()

def get_service():
    return NL2SQLService()

@router.get("/schema/{datasource_id}", response_model=SchemaResponse)
async def get_schema(datasource_id: str):
    try:
        service = get_service()
        return service.get_schema(datasource_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/schema", response_model=list)
async def list_schemas():
    try:
        # Get the list of registered datasources from the service
        service = get_service()
        datasource_ids = service.list_datasources()
        return datasource_ids
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))