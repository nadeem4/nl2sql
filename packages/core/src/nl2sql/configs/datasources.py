
from typing import Optional, Dict, Any, Union, List
from pydantic import BaseModel, Field

class ConnectionConfig(BaseModel):
    """Database connection details."""
    type: str 
    
    model_config = {"extra": "allow"}

class DatasourceConfig(BaseModel):
    """Configuration for a single datasource."""
    id: str
    description: Optional[str] = None
    connection: ConnectionConfig
    options: Dict[str, Any] = Field(default_factory=dict)

class DatasourceFileConfig(BaseModel):
    """File-level schema for datasources.yaml."""
    version: int = Field(1, description="Schema version")
    datasources: List[DatasourceConfig]
