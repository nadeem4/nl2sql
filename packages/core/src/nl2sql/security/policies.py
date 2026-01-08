from typing import List, Dict, Optional
from pydantic import BaseModel, Field, RootModel, field_validator

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
            
            # Check for datasource wildcard definition (e.g. "sales_db.*")
            if table.endswith(".*"):
                # Basic check for structure "ds.*"
                if table.count(".") < 1:
                     raise ValueError(f"Invalid wildcard '{table}'. Must be 'datasource.*'.")
                continue

            # Standard Table check
            if "." not in table:
                raise ValueError(f"Invalid table '{table}'. Policy requires explicit 'datasource.table' format to prevent ambiguity.")
        
        return v

class PolicyConfig(RootModel):
    """Root configuration map: Role ID -> RolePolicy"""
    root: Dict[str, RolePolicy]

    def get_role(self, role_id: str) -> Optional[RolePolicy]:
        return self.root.get(role_id)
