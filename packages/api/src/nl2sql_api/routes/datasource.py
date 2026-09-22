from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse
from nl2sql_api.dependencies import get_datasource_service
from nl2sql_api.services import DatasourceService

router = APIRouter(tags=["datasources"])

DatasourceSvc = Annotated[DatasourceService, Depends(get_datasource_service)]


@router.post("/datasource", response_model=DatasourceResponse, summary="Add a datasource")
def add_datasource(
    payload: DatasourceRequest,
    service: DatasourceSvc,
):
    """Register a datasource in this process from `config`, shaped like one entry of
    `configs/datasources.yaml`. It is not written to the config file and not indexed:
    call `POST /api/v1/index/{datasource_id}` next.
    """
    try:
        return service.add_datasource(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource", response_model=Dict[str, Any], summary="List datasources")
def list_datasources(
    service: DatasourceSvc,
):
    """The ids of the registered datasources, as `{"datasources": [...]}`."""
    try:
        return {"datasources": service.list_datasources()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Check a datasource exists")
def get_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    """`{"datasource_id": ..., "exists": true}`, or 404 when it is not registered. No other details yet."""
    try:
        return service.get_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Remove a datasource (not supported)")
def remove_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    """Not supported by the engine yet: answers `success: false` (404 for an unknown id)."""
    try:
        return service.remove_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
