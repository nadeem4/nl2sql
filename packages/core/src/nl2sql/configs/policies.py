
from typing import List, Dict, Optional
from pydantic import BaseModel, Field, field_validator
from nl2sql.auth import RolePolicy




class PolicyFileConfig(BaseModel):
    """File-level schema for policies.json."""
    version: int = Field(1, description="Schema version")
    roles: Dict[str, RolePolicy]


