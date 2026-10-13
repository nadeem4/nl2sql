import logging

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from pydantic import ConfigDict

logger = logging.getLogger(__name__)

# The two-part entry warning is logged once per process, not once per policy.
_warned_legacy_entry = False

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
    allowed_tables: List[str] = Field(
        default_factory=list,
        description=(
            "Allowed tables: '*', 'datasource.*', 'datasource.schema.*', 'datasource.schema.table', "
            "or the legacy 'datasource.table' (that table in the datasource's default schema)"
        ),
    )

    @field_validator("allowed_tables")
    def validate_namespace(cls, v: List[str]) -> List[str]:
        """Enforces strict namespacing for allowed tables.

        Every part is non-empty and ``*`` may only stand alone or be the last
        part. A two-part ``datasource.table`` is accepted and read as that
        table in the datasource's default schema; the first one seen logs a
        warning saying so, once per process.
        """
        global _warned_legacy_entry
        for table in v:
            if table == "*":
                continue
            parts = table.split(".")
            if len(parts) not in (2, 3) or any(not p for p in parts) or "*" in parts[:-1]:
                raise ValueError(
                    f"Invalid table '{table}'. Use 'datasource.*', 'datasource.schema.*', "
                    "'datasource.schema.table' or 'datasource.table'."
                )
            if len(parts) == 2 and parts[1] != "*" and not _warned_legacy_entry:
                _warned_legacy_entry = True
                logger.warning(
                    "Policy entry '%s' has no schema: two-part 'datasource.table' entries are read as "
                    "that table in the datasource's default schema only. Write 'datasource.schema.table' "
                    "to grant a table in another schema.",
                    table,
                )
        return v
