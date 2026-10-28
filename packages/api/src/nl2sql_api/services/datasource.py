from dataclasses import asdict
from typing import Any, Dict, List, Sequence, Tuple, Type

from pydantic import BaseModel, ValidationError

from nl2sql import NL2SQL, ConnectionInvalid, Datasource, UserContext
from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse, DatasourceTestRequest


class BadRequest(ValueError):
    """The body does not validate. ``problems`` lists each one by path, never a value."""

    def __init__(self, problems: List[Dict[str, str]]):
        self.problems = problems
        super().__init__("; ".join(f"{p['path']}: {p['message']}" for p in problems))


def _problems(error: ValidationError, prefix: Sequence[str] = ()) -> List[Dict[str, str]]:
    return [{"path": ".".join(str(part) for part in (*prefix, *problem["loc"])), "message": problem["msg"]}
            for problem in error.errors(include_input=False, include_url=False)]


def _request(model: Type[BaseModel], body: Any) -> Tuple[Any, Datasource]:
    """The request and its datasource, or a ``BadRequest`` listing every problem by its path in the body."""
    if not isinstance(body, dict):
        raise BadRequest([{"path": "", "message": "The body must be a JSON object."}])
    try:
        request = model.model_validate(body)
    except ValidationError as error:
        raise BadRequest(_problems(error)) from None
    try:
        return request, Datasource.model_validate(request.config)
    except ValidationError as error:
        raise BadRequest(_problems(error, ("config",))) from None


class DatasourceService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def add_datasource(self, body: Any, *, by: UserContext, env_file: bool) -> DatasourceResponse:
        """Add a datasource; ``config`` has the shape of one ``datasources`` entry in ``nl2sql.yaml``.

        With ``save`` it is also written to ``datasources/<id>.yaml`` as ``by``; when that fails the datasource is
        taken out again, so a datasource that was not saved is not left running.

        Raises:
            BadRequest: the body does not validate.
        """
        request, datasource = _request(DatasourceRequest, body)
        try:
            self.engine.datasources.add(datasource)
        except ConnectionInvalid as error:
            raise BadRequest([{"path": f"config.{p['path']}", "message": p["message"]} for p in error.problems]) from None
        file = None
        if request.save:
            try:
                path = self.engine.datasources.save(datasource.id, by=by, env_file=env_file)
            except Exception:
                self.engine.datasources.remove(datasource.id)
                raise
            file = f"{path.parent.name}/{path.name}"
        return DatasourceResponse(
            success=True,
            message=f"Datasource '{datasource.id}' added successfully" + (" and saved" if file else ""),
            datasource_id=datasource.id,
            file=file,
        )

    def test_datasource(self, body: Any) -> Dict[str, Any]:
        """Connect to a datasource that is not added and report the stage that failed, if one did.

        Raises:
            BadRequest: the body does not validate.
        """
        _, datasource = _request(DatasourceTestRequest, body)
        return asdict(self.engine.datasources.test(datasource))

    def datasource_types(self) -> List[Dict[str, Any]]:
        """Every adapter the engine can name, installed or not, with its connection schema."""
        return [asdict(kind) for kind in self.engine.datasources.types()]

    def list_datasources(self) -> List[Dict[str, Any]]:
        """Every configured datasource, with its status; connections masked."""
        return [asdict(info) for info in self.engine.datasources.list()]

    def get_datasource(self, datasource_id: str) -> Dict[str, Any]:
        """One datasource, its connection masked; ``ValueError`` for an unknown id."""
        return asdict(self.engine.datasources.get(datasource_id))

    def remove_datasource(self, datasource_id: str) -> Dict[str, Any]:
        """Remove a datasource from the engine; ``ValueError`` for an unknown id."""
        self.engine.datasources.remove(datasource_id)
        return {"success": True, "message": f"Datasource '{datasource_id}' removed", "datasource_id": datasource_id}
