from pydantic import BaseModel, Field
from typing import List, Optional
from pydantic import ConfigDict

class UserContext(BaseModel):
    """User identity and permission context."""
    user_id: Optional[str] = Field(default=None, description="Unique identifier for the user.")
    tenant_id: Optional[str] = Field(default=None, description="Organization/Tenant identifier.")
    roles: List[str] = Field(default_factory=list, description="List of assigned roles.")
    model_config = ConfigDict(extra="ignore")