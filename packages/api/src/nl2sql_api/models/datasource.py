from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, Optional


class DatasourceTestRequest(BaseModel):
    """``POST /datasource/test``: one datasource, shaped like one entry of `datasources` in `nl2sql.yaml`."""

    model_config = ConfigDict(extra="forbid")

    config: Dict[str, Any]


class DatasourceRequest(DatasourceTestRequest):
    """``POST /datasource``: the datasource, and whether to also write it to `datasources/<id>.yaml`."""

    save: bool = False


class DatasourceResponse(BaseModel):
    success: bool
    message: str
    datasource_id: Optional[str] = None
    file: Optional[str] = None
