from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse
from nl2sql_api.dependencies import get_datasource_service
from nl2sql_api.services import DatasourceService

router = APIRouter()

DatasourceSvc = Annotated[DatasourceService, Depends(get_datasource_service)]


@router.post("/datasource", response_model=DatasourceResponse)
async def add_datasource(
    payload: DatasourceRequest,
    service: DatasourceSvc,
):
    try:
        return service.add_datasource(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource", response_model=Dict[str, Any])
async def list_datasources(
    service: DatasourceSvc,
):
    try:
        return {"datasources": service.list_datasources()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource/{datasource_id}", response_model=Dict[str, Any])
async def get_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    try:
        return service.get_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/datasource/{datasource_id}", response_model=Dict[str, Any])
async def remove_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    try:
        return service.remove_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
