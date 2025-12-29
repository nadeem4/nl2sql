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


class ColumnInfo(BaseModel):
    """
    Represents a column in a table with its type.
    """
    model_config = ConfigDict(extra="forbid")
    name: str # Pre-aliased name, e.g. "t1.id"
    original_name: str # Original name, e.g. "id"
    type: str # SQL Type, e.g. "INTEGER", "VARCHAR"


class TableInfo(BaseModel):
    """
    Represents schema information for a single table.

    Attributes:
        name: The actual name of the table in the database.
        alias: The alias assigned to the table (e.g., "t1").
        columns: List of column information.
        foreign_keys: List of foreign keys defined on this table.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str
    columns: List[ColumnInfo]
    foreign_keys: List[ForeignKey] = Field(default_factory=list)


class SchemaInfo(BaseModel):
    """
    Represents the full schema information available to the planner.

    Attributes:
        tables: List of table information.
    """
    tables: List[TableInfo] = Field(default_factory=list)
