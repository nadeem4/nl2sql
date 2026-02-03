from pydantic import BaseModel
from typing import Dict, Any, Optional

class DatasourceRequest(BaseModel):
    config: Dict[str, Any]


class DatasourceResponse(BaseModel):
    success: bool
    message: str
    datasource_id: Optional[str] = None