from pydantic import BaseModel, ConfigDict
from typing import Dict, Any, Optional


class DatasourceTestRequest(BaseModel):
    """``POST /datasource/test``: one datasource, shaped like one entry of `datasources` in `nl2sql.yaml`."""

    model_config = ConfigDict(extra="forbid")

    config: Dict[str, Any]
    # Read by `auth.get_user_context` under the dev flag `api.trust_body_role`; the route itself ignores it.
    user_context: Optional[Dict[str, Any]] = None


class DatasourceRequest(DatasourceTestRequest):
    """``POST /datasource``: the datasource, and whether to also write it to `datasources/<id>.yaml`."""

    save: bool = False


class DatasourceResponse(BaseModel):
    success: bool
    message: str
    datasource_id: Optional[str] = None
    file: Optional[str] = None
