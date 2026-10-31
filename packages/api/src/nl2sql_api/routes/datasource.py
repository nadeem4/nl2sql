from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, Request

from nl2sql import NL2SQL, NL2SQLError, UserContext
from nl2sql_api import identity
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse, DatasourceTestRequest
from nl2sql_api.dependencies import get_datasource_service, get_engine
from nl2sql_api.services import DatasourceService
from nl2sql_api.services.datasource import BadRequest

router = APIRouter(tags=["datasources"])

DatasourceSvc = Annotated[DatasourceService, Depends(get_datasource_service)]

ADD_EXAMPLE = {
    "config": {"id": "sales", "type": "postgres", "description": "Orders and customers",
               "connection": {"host": "db.internal", "database": "sales", "user": "nl2sql",
                              "password": "${env:SALES_DB_PASSWORD}"}},
    "save": True,
}


async def _json_body(request: Request) -> Any:
    """The request's JSON, whatever it is. Not FastAPI's own body model, whose 422 echoes the input: a body that is
    not JSON or not an object is answered with the error envelope, no input in it."""
    try:
        return await request.json()
    except Exception:
        raise RequestRefused(422, "INVALID_REQUEST", "The body is not valid JSON.",
                             details=[{"loc": ["body"], "msg": "The body is not valid JSON."}]) from None


def _body_docs(model: Any, example: Dict[str, Any]) -> Dict[str, Any]:
    return {"requestBody": {"required": True, "content": {"application/json": {
        "schema": model.model_json_schema(), "examples": {"postgres": {"value": example}}}}}}


PROBLEMS_422 = {422: {**ERROR_RESPONSE, "description": "The body does not validate: `error.details` lists each "
                                                       "problem as `{loc, msg}` (never a value)."}}


def _problems(error: BadRequest) -> RequestRefused:
    return RequestRefused(422, "INVALID_REQUEST", "The datasource does not validate.",
                          fix="Fix the fields listed in details.",
                          details=[{"loc": p["path"].split("."), "msg": p["message"]} for p in error.problems])


@router.get("/datasource-types", response_model=Dict[str, Any], summary="List the datasource types",
            dependencies=[Depends(identity.admin)], responses=identity.ADMIN_RESPONSES)
def datasource_types(
    service: DatasourceSvc,
):
    """Every adapter the engine can name, installed or not, as `{"types": [...]}`: its `name`, `title`,
    `installed`, an `install_hint` when a driver is missing and the JSON Schema of its connection
    (`connection_schema`), which is the Connect a database form: a secret field is `format: password`.
    Admin only."""
    return {"types": service.datasource_types()}


@router.post("/datasource/test", response_model=Dict[str, Any], summary="Test a datasource",
             dependencies=[Depends(identity.admin)], responses={**identity.ADMIN_RESPONSES, **PROBLEMS_422},
             openapi_extra=_body_docs(DatasourceTestRequest, {"config": ADD_EXAMPLE["config"]}))
def test_datasource(
    service: DatasourceSvc,
    payload: Annotated[Any, Depends(_json_body)],
):
    """Connect to the datasource in `config` and say whether it works: `{"id", "status": "ready"|"failed",
    "stage", "reason", "fix", "warnings"}`. A failure is a 200 with `status: failed`, its `stage` (`config`,
    `driver`, `network`, `auth`, `database`, `permission`) and what to do. Nothing is registered or written,
    and no value is echoed. Admin only."""
    try:
        return service.test_datasource(payload)
    except BadRequest as e:
        raise _problems(e) from None


@router.post("/datasource", response_model=DatasourceResponse, summary="Add a datasource",
             responses={**identity.ADMIN_RESPONSES, **PROBLEMS_422,
                        409: {**ERROR_RESPONSE, "description": "The id is taken, or `save` found the "
                                                               "datasource's file changed on disk."}},
             openapi_extra=_body_docs(DatasourceRequest, ADD_EXAMPLE))
def add_datasource(
    service: DatasourceSvc,
    user: UserContext = Depends(identity.admin),
    engine: NL2SQL = Depends(get_engine),
    payload: Any = Depends(_json_body),
):
    """Register a datasource in this process from `config`, shaped like one entry of
    `datasources` in `nl2sql.yaml` (`id`, `type`, `connection`, ...). It is not indexed: call
    `POST /api/v1/index/{datasource_id}` next. Admin only.

    With `"save": true` it is also written to `datasources/<id>.yaml` beside `nl2sql.yaml`, and `file` says
    where. A secret is never written into the YAML: a `${env:VAR}` or other reference is kept as it is, and a
    literal is written to the `.env` as `${env:NL2SQL_...}` only when `api.auth` is `none` (the local operator);
    anywhere else it is a 422. A save that fails leaves nothing registered.

    A body that does not validate, a typo included, is a 422 listing each problem by its path, never a value.
    """
    try:
        return service.add_datasource(payload, by=user, env_file=identity.is_local_operator(engine))
    except BadRequest as e:
        raise _problems(e) from None
    except NL2SQLError:
        raise  # a named refusal (a taken id, a literal secret, a stale file, a bad connection) says its own status


@router.get("/datasource", response_model=Dict[str, Any], summary="List datasources")
def list_datasources(
    service: DatasourceSvc,
    user: UserContext = Depends(identity.caller),
):
    """Every configured datasource as `{"datasources": [...]}`: its `id`, `type`, `description`,
    `status` (`ready`, `failed` with a `reason`, or `unchecked`), `capabilities` and `connection`,
    with every secret masked."""
    return {"datasources": service.list_datasources(user)}


NOT_FOUND_404 = {404: {**ERROR_RESPONSE, "description": "No datasource has that id."}}


@router.get("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Get a datasource",
            responses=NOT_FOUND_404)
def get_datasource(
    datasource_id: str,
    service: DatasourceSvc,
    user: UserContext = Depends(identity.caller),
):
    """One configured datasource, as the list shows it, or 404 when it is not configured or the caller cannot reach it."""
    return service.get_datasource(datasource_id, user)


@router.delete("/datasource/{datasource_id}", response_model=Dict[str, Any], summary="Remove a datasource",
               dependencies=[Depends(identity.admin)], responses={**identity.ADMIN_RESPONSES, **NOT_FOUND_404})
def remove_datasource(
    datasource_id: str,
    service: DatasourceSvc,
):
    """Take a datasource out of the engine and close its connections (404 for an unknown id). It is not
    removed from the config file, and its index entries stay until the next full index. Admin only."""
    return service.remove_datasource(datasource_id)
