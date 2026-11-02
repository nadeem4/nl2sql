from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, ConfigDict, ValidationError

from nl2sql import Datasource
from nl2sql_api.errors import RequestRefused


class DatasourceTestRequest(BaseModel):
    """``POST /datasources/test``: one datasource, shaped like one entry of `datasources` in `nl2sql.yaml`."""

    model_config = ConfigDict(extra="forbid")

    config: Dict[str, Any]


class DatasourceRequest(DatasourceTestRequest):
    """``POST /datasources``: the datasource, and whether to also write it to `datasources/<id>.yaml`."""

    save: bool = False


class DatasourceResponse(BaseModel):
    success: bool
    message: str
    datasource_id: Optional[str] = None
    file: Optional[str] = None


def refused(problems: List[Dict[str, Any]]) -> RequestRefused:
    """A 422 listing each problem by its path, never a value."""
    return RequestRefused(422, "INVALID_REQUEST", "The datasource does not validate.",
                          fix="Fix the fields listed in details.",
                          details=[{"loc": p["loc"], "msg": p["msg"]} for p in problems])


def _problems(error: ValidationError, prefix: Sequence[str] = ()) -> List[Dict[str, Any]]:
    return [{"loc": [*prefix, *problem["loc"]], "msg": problem["msg"]}
            for problem in error.errors(include_input=False, include_url=False)]


def parse(model: Type[BaseModel], body: Any) -> Tuple[Any, Datasource]:
    """The request and its datasource, or a 422 listing every problem by its path in the body."""
    if not isinstance(body, dict):
        raise refused([{"loc": [""], "msg": "The body must be a JSON object."}])
    try:
        request = model.model_validate(body)
    except ValidationError as error:
        raise refused(_problems(error)) from None
    try:
        return request, Datasource.model_validate(request.config)
    except ValidationError as error:
        raise refused(_problems(error, ("config",))) from None
