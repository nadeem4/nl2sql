from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class SchemaResponse(BaseModel):
    datasource_id: str
    tables: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]] = None