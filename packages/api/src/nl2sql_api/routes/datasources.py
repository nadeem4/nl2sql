"""The `datasources` namespace of `/api/v1`: Connect and the reads
a page needs. Thin over ``engine.datasources``; the caller is passed to every read, so a datasource or a table the
caller's scope hides is not found. A failure is the facade's named error, mapped by the envelope."""
from dataclasses import asdict
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from nl2sql import ConnectionInvalid, DatasourceInfo, NL2SQL, NL2SQLError, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.models.datasources import (DatasourceRequest, DatasourceResponse, DatasourceTestRequest, parse,
                                           refused)

router = APIRouter(tags=["datasources"])

Engine = Annotated[NL2SQL, Depends(get_engine)]
Caller = Annotated[UserContext, Depends(identity.caller)]

ADD_EXAMPLE = {
    "config": {"id": "sales", "type": "postgres", "description": "Orders and customers",
               "connection": {"host": "db.internal", "database": "sales", "user": "nl2sql",
                              "password": "${env:SALES_DB_PASSWORD}"}},
    "save": True,
}
NOT_FOUND_404 = {404: {**ERROR_RESPONSE, "description": "No datasource has that id (or the caller cannot reach it)."}}
PROBLEMS_422 = {422: {**ERROR_RESPONSE, "description": "The body does not validate: `error.details` lists each "
                                                       "problem as `{loc, msg}` (never a value)."}}


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


@router.get("/datasource-types", response_model=Dict[str, Any], summary="List the datasource types",
            dependencies=[Depends(identity.admin)], responses=identity.ADMIN_RESPONSES)
def datasource_types(engine: Engine):
    """Every adapter the engine can name, installed or not, as `{"types": [...]}`: its `name`, `title`,
    `installed`, an `install_hint` when a driver is missing and the JSON Schema of its connection
    (`connection_schema`), which is the Connect a database form: a secret field is `format: password`.
    Admin only."""
    return {"types": [asdict(kind) for kind in engine.datasources.types()]}


@router.post("/datasources/test", response_model=Dict[str, Any], summary="Test a datasource",
             dependencies=[Depends(identity.admin)], responses={**identity.ADMIN_RESPONSES, **PROBLEMS_422},
             openapi_extra=_body_docs(DatasourceTestRequest, {"config": ADD_EXAMPLE["config"]}))
def test_datasource(engine: Engine, payload: Annotated[Any, Depends(_json_body)]):
    """Connect to the datasource in `config` and say whether it works: `{"id", "status": "ready"|"failed",
    "stage", "reason", "fix", "warnings"}`. A failure is a 200 with `status: failed`, its `stage` (`config`,
    `driver`, `network`, `auth`, `database`, `permission`) and what to do. Nothing is registered or written,
    and no value is echoed. Admin only."""
    _, datasource = parse(DatasourceTestRequest, payload)
    return asdict(engine.datasources.test(datasource))


@router.post("/datasources", response_model=DatasourceResponse, summary="Add a datasource",
             responses={**identity.ADMIN_RESPONSES, **PROBLEMS_422,
                        403: {**ERROR_RESPONSE, "description": "Not an admin, or `save` found the config "
                                                               "read-only (`NOT_WRITABLE`)."},
                        409: {**ERROR_RESPONSE, "description": "The id is taken, or `save` found the "
                                                               "datasource's file changed on disk."}},
             openapi_extra=_body_docs(DatasourceRequest, ADD_EXAMPLE))
def add_datasource(engine: Engine, user: UserContext = Depends(identity.admin),
                   payload: Any = Depends(_json_body)):
    """Register a datasource in this process from `config`, shaped like one entry of `datasources` in
    `nl2sql.yaml` (`id`, `type`, `connection`, ...). It is not indexed: start the `index` job
    (`POST /jobs/index`) next. Admin only.

    With `"save": true` it is also written to `datasources/<id>.yaml` beside `nl2sql.yaml`, and `file` says
    where. A secret is never written into the YAML: a `${env:VAR}` or other reference is kept as it is, and a
    literal is written to the `.env` as `${env:NL2SQL_...}` only when `api.auth` is `none` (the local operator);
    anywhere else it is a 422. A save that fails leaves nothing registered, except when the config is
    read-only (`403 NOT_WRITABLE`, Docker's mounted config): the datasource then stays registered until the
    server restarts.

    A body that does not validate, a typo included, is a 422 listing each problem by its path, never a value.
    """
    request, datasource = parse(DatasourceRequest, payload)
    try:
        engine.datasources.add(datasource)
    except ConnectionInvalid as error:
        raise refused([{"loc": ["config", *p["path"].split(".")], "msg": p["message"]}
                       for p in error.problems]) from None
    file: Optional[str] = None
    if request.save:
        try:
            path = engine.datasources.save(datasource.id, by=user, env_file=identity.is_local_operator(engine))
        except NL2SQLError as error:
            if error.code != "NOT_WRITABLE":  # a read-only config keeps the datasource for this process
                engine.datasources.remove(datasource.id)
            raise
        except Exception:
            engine.datasources.remove(datasource.id)
            raise
        file = f"{path.parent.name}/{path.name}"
    return DatasourceResponse(
        success=True,
        message=f"Datasource '{datasource.id}' added successfully" + (" and saved" if file else ""),
        datasource_id=datasource.id, file=file)


@router.get("/datasources", response_model=List[DatasourceInfo], summary="List datasources",
            responses=identity.AUTH_RESPONSES)
def list_datasources(engine: Engine, user: Caller):
    """The datasources the caller's scope reaches: `id`, `type`, `description`, `status` (`ready`, `failed` with a
    `reason` (a generic sentence unless the caller is an admin: the driver's text stays with admins), or `unchecked`), `capabilities`, `connection` (every secret masked) and `sample_questions`."""
    return engine.datasources.list(user_context=user)


@router.get("/datasources/{datasource_id}", response_model=DatasourceInfo, summary="Get a datasource",
            responses={**NOT_FOUND_404, **identity.AUTH_RESPONSES})
def get_datasource(datasource_id: str, engine: Engine, user: Caller):
    """One datasource, as the list shows it, or 404 when it is not configured or the caller cannot reach it."""
    return engine.datasources.get(datasource_id, user_context=user)


@router.delete("/datasources/{datasource_id}", response_model=Dict[str, Any], summary="Remove a datasource",
               dependencies=[Depends(identity.admin)], responses={**identity.ADMIN_RESPONSES, **NOT_FOUND_404})
def remove_datasource(datasource_id: str, engine: Engine):
    """Take a datasource out of the engine and close its connections (404 for an unknown id). It is not
    removed from the config file, and its index entries stay until the next full index. Admin only."""
    engine.datasources.remove(datasource_id)
    return {"success": True, "message": f"Datasource '{datasource_id}' removed", "datasource_id": datasource_id}


@router.get("/datasources/{datasource_id}/schema", response_model=Dict[str, Any], summary="Read a schema",
            responses={**NOT_FOUND_404, **identity.AUTH_RESPONSES})
def get_schema(datasource_id: str, engine: Engine, user: Caller):
    """The datasource's tables and columns as the caller may see them: a table or column the caller's scope
    denies is left out."""
    engine.datasources.get(datasource_id, user_context=user)  # 404 for an unknown or hidden id, as /datasources/{id}
    return engine.datasources.schema(datasource_id, user_context=user)


@router.get("/datasources/{datasource_id}/diagram", response_class=PlainTextResponse, summary="Draw a schema",
            responses={200: {"content": {"text/plain": {}}, "description": "A Mermaid `erDiagram`."},
                       422: {**ERROR_RESPONSE, "description": "`tables` names no table the caller may see."},
                       **NOT_FOUND_404, **identity.AUTH_RESPONSES})
def get_diagram(datasource_id: str, engine: Engine, user: Caller,
                tables: Annotated[Optional[List[str]], Query()] = None):
    """The schema as a Mermaid entity-relationship diagram, for the caller's view of it. Repeat `tables` to draw
    those tables and the ones one relationship away; leave it out for the whole datasource (cut to the 30 with the
    most relationships)."""
    engine.datasources.get(datasource_id, user_context=user)  # 404 for an unknown or hidden id
    return engine.datasources.diagram(datasource_id, user_context=user, tables=tables)
