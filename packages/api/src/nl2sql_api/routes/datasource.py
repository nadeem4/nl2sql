import logging

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api import auth
from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse
from nl2sql_api.dependencies import get_datasource_service
from nl2sql_api.services import DatasourceService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["datasources"])

DatasourceSvc = Annotated[DatasourceService, Depends(get_datasource_service)]


@router.post("/datasource", response_model=DatasourceResponse, summary="Add a datasource",
             dependencies=[Depends(auth.require_admin)], responses=auth.ADMIN_RESPONSES)
def add_datasource(
    payload: DatasourceRequest,
    service: DatasourceSvc,
):
    """Register a datasource in this process from `config`, shaped like one entry of
    `datasources` in `nl2sql.yaml` (`id`, `type`, `connection`, ...). It is not
    written to the config file and not indexed: call
    `POST /api/v1/index/{datasource_id}` next. Admin only.
    """
    try:
        return service.add_datasource(payload)
    except Exception:
        # The exception can carry the connection string, password included: log it, never return it.
        logger.exception("Failed to add a datasource")
        raise HTTPException(status_code=500,
                            detail="Failed to add the datasource. The cause is in the server log.")


@router.get("/datasource", response_model=Dict[str, Any], summary="List datasources")
def list_datasources(
    service: DatasourceSvc,
):
    """Every configured datasource as `{"datasources": [...]}`: its `id`, `type`, `description`,
    `status` (`ready`, `failed` with a `reason`, or `unchecked`), `capabilities` and `connection`,
    with every secret masked."""
    try:
        return {"datasources": service.list_datasources()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Get a datasource")
def get_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    """One configured datasource, as the list shows it, or 404 when it is not configured."""
    try:
        return service.get_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Remove a datasource",
               dependencies=[Depends(auth.require_admin)], responses=auth.ADMIN_RESPONSES)
def remove_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    """Take a datasource out of the engine and close its connections (404 for an unknown id). It is not
    removed from the config file, and its index entries stay until the next full index. Admin only."""
    try:
        return service.remove_datasource(datasource_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
