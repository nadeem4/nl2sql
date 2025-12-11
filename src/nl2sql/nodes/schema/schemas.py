from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

class ForeignKey(BaseModel):
    """
    Represents a foreign key relationship.

    Attributes:
        column: The column in the source table.
        referred_table: The table referenced by the foreign key.
        referred_column: The column in the referred table.
    """
    model_config = ConfigDict(extra="forbid")
    column: str
    referred_table: str
    referred_column: str


class TableInfo(BaseModel):
    """
    Represents schema information for a single table.

    Attributes:
        name: The actual name of the table in the database.
        alias: The alias assigned to the table (e.g., "t1").
        columns: List of column names (pre-aliased, e.g., "t1.id").
        foreign_keys: List of foreign keys defined on this table.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str
    columns: List[str]
    foreign_keys: List[ForeignKey] = Field(default_factory=list)


class SchemaInfo(BaseModel):
    """
    Represents the full schema information available to the planner.

    Attributes:
        tables: List of table information.
    """
    tables: List[TableInfo] = Field(default_factory=list)
