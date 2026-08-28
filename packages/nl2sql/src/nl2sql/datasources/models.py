

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


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



