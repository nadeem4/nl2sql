from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse

router = APIRouter()


@router.post("/datasource", response_model=DatasourceResponse)
async def add_datasource(request: Request, payload: DatasourceRequest):
    try:
        service = request.app.state.container.datasource
        return service.add_datasource(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource", response_model=Dict[str, Any])
async def list_datasources(request: Request):
    try:
        service = request.app.state.container.datasource
        return {"datasources": service.list_datasources()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource/{datasource_id}", response_model=Dict[str, Any])
async def get_datasource(request: Request, datasource_id: str):
    try:
        service = request.app.state.container.datasource
        return service.get_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/datasource/{datasource_id}", response_model=Dict[str, Any])
async def remove_datasource(request: Request, datasource_id: str):
    try:
        service = request.app.state.container.datasource
        return service.remove_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
