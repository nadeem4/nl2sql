import logging

from fastapi import APIRouter, Body, HTTPException, Depends, Request
from typing import Dict, Any, Annotated

from nl2sql import NL2SQL, ReviewError, UserContext
from nl2sql_api import auth
from nl2sql_api.models.datasource import DatasourceResponse
from nl2sql_api.dependencies import get_datasource_service, get_engine
from nl2sql_api.services import DatasourceService
from nl2sql_api.services.datasource import BadRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["datasources"])

DatasourceSvc = Annotated[DatasourceService, Depends(get_datasource_service)]

ADD_EXAMPLE = {
    "config": {"id": "sales", "type": "postgres", "description": "Orders and customers",
               "connection": {"host": "db.internal", "database": "sales", "user": "nl2sql",
                              "password": "${env:SALES_DB_PASSWORD}"}},
    "save": True,
}
PROBLEMS_422 = {422: {"description": "The body does not validate: `detail` lists each problem as `{path, message}` "
                                     "(never a value), or says why the datasource could not be added or saved."}}


@router.get("/datasource-types", response_model=Dict[str, Any], summary="List the datasource types",
            dependencies=[Depends(auth.require_admin)], responses=auth.ADMIN_RESPONSES)
def datasource_types(
    service: DatasourceSvc,
):
    """Every adapter the engine can name, installed or not, as `{"types": [...]}`: its `name`, `title`,
    `installed`, an `install_hint` when a driver is missing and the JSON Schema of its connection
    (`connection_schema`), which is the Connect a database form: a secret field is `format: password`.
    Admin only."""
    try:
        return {"types": service.datasource_types()}
    except Exception:
        logger.exception("Failed to list the datasource types")
        raise HTTPException(status_code=500, detail="Failed to list the datasource types. The cause is in the server log.")


@router.post("/datasource/test", response_model=Dict[str, Any], summary="Test a datasource",
             dependencies=[Depends(auth.require_admin)], responses={**auth.ADMIN_RESPONSES, **PROBLEMS_422})
def test_datasource(
    service: DatasourceSvc,
    payload: Annotated[Dict[str, Any], Body(openapi_examples={"postgres": {"value": {"config": ADD_EXAMPLE["config"]}}})],
):
    """Connect to the datasource in `config` and say whether it works: `{"id", "status": "ready"|"failed",
    "stage", "reason", "fix", "warnings"}`. A failure is a 200 with `status: failed`, its `stage` (`config`,
    `driver`, `network`, `auth`, `database`, `permission`) and what to do. Nothing is registered or written,
    and no value is echoed. Admin only."""
    try:
        return service.test_datasource(payload)
    except BadRequest as e:
        raise HTTPException(status_code=422, detail=e.problems)
    except Exception:
        logger.exception("Failed to test a datasource")
        raise HTTPException(status_code=500, detail="Failed to test the datasource. The cause is in the server log.")


@router.post("/datasource", response_model=DatasourceResponse, summary="Add a datasource",
             responses={**auth.ADMIN_RESPONSES, **PROBLEMS_422,
                        409: {"description": "`save` found the datasource's file changed on disk since the "
                                             "engine loaded it."}})
def add_datasource(
    request: Request,
    service: DatasourceSvc,
    payload: Annotated[Dict[str, Any], Body(openapi_examples={"postgres": {"value": ADD_EXAMPLE}})],
    user: UserContext = Depends(auth.require_admin),
    engine: NL2SQL = Depends(get_engine),
):
    """Register a datasource in this process from `config`, shaped like one entry of
    `datasources` in `nl2sql.yaml` (`id`, `type`, `connection`, ...). It is not indexed: call
    `POST /api/v1/index/{datasource_id}` next. Admin only.

    With `"save": true` it is also written to `datasources/<id>.yaml` beside `nl2sql.yaml`, and `file` says
    where. A secret is never written into the YAML: a `${env:VAR}` or other reference is kept as it is, and a
    literal is written to the `.env` as `${env:NL2SQL_...}` only on a fresh local install (no role source
    configured, loopback client); anywhere else it is a 422. A save that fails leaves nothing registered.

    A body that does not validate, a typo included, is a 422 listing each problem by its path, never a value.
    """
    try:
        return service.add_datasource(payload, by=user, env_file=auth.is_fresh_local_install(request, engine))
    except BadRequest as e:
        raise HTTPException(status_code=422, detail=e.problems)
    except ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:  # an id that is taken, a connection or index that does not validate, a secret literal
        raise HTTPException(status_code=422, detail=str(e))
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
