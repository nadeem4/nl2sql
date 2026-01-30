
from typing import Optional, Dict, Any, Union, List
from pydantic import BaseModel, Field
from nl2sql.datasources import DatasourceConfig, ConnectionConfig


class DatasourceFileConfig(BaseModel):
    """File-level schema for datasources.yaml."""
    version: int = Field(1, description="Schema version")
    datasources: List[DatasourceConfig]
