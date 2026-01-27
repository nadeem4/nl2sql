from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from pydantic import ConfigDict

class UserContext(BaseModel):
    """User identity and permission context."""
    user_id: Optional[str] = Field(default=None, description="Unique identifier for the user.")
    tenant_id: Optional[str] = Field(default=None, description="Organization/Tenant identifier.")
    roles: List[str] = Field(default_factory=list, description="List of assigned roles.")
    model_config = ConfigDict(extra="ignore")


class RolePolicy(BaseModel):
    """Defines access control rules for a specific role."""
    
    description: str = Field(..., description="Human-readable description of the role")
    role: str = Field(..., description="Role ID used for logging and auditing")
    allowed_datasources: List[str] = Field(default_factory=list, description="List of allowed datasource IDs or '*'")
    allowed_tables: List[str] = Field(default_factory=list, description="List of allowed tables in 'datasource.table' format")

    @field_validator("allowed_tables")
    def validate_namespace(cls, v: List[str]) -> List[str]:
        """Enforces strict namespacing for allowed tables."""
        for table in v:
            if table == "*":
                continue
            
            if table.endswith(".*"):
                if table.count(".") < 1:
                     raise ValueError(f"Invalid wildcard '{table}'. Must be 'datasource.*'.")
                continue

            if "." not in table:
                raise ValueError(f"Invalid table '{table}'. Policy requires explicit 'datasource.table' format to prevent ambiguity.")
        
        return v