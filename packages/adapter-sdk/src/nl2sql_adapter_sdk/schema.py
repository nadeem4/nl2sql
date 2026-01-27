from __future__ import annotations

from typing import Dict, List, Optional, Union, Literal

from pydantic import BaseModel, ConfigDict, Field

Scalar = Union[str, int, float, bool, None]
JsonValue = Union[Scalar, List[Scalar], Dict[str, Scalar]]


class Column(BaseModel):
    """Lightweight column schema for routing/planning."""

    name: str
    type: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class Table(BaseModel):
    """Lightweight table schema for routing/planning."""

    name: str
    columns: List[Column] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class TableRef(BaseModel):
    schema_name: str
    table_name: str

    model_config = ConfigDict(extra="ignore", frozen=True)

    @property
    def full_name(self) -> str:
        return f"[{self.schema_name}].[{self.table_name}]"


class ColumnStatistics(BaseModel):
    null_percentage: float
    distinct_count: int
    min_value: Optional[Scalar] = None
    max_value: Optional[Scalar] = None
    sample_values: List[JsonValue] = Field(default_factory=list)

    def __str__(self) -> str:
        return (
            "null_percentage: "
            f"{self.null_percentage}, distinct_count: {self.distinct_count}, "
            f"min_value: {self.min_value}, max_value: {self.max_value}, "
            f"sample_values: {self.sample_values}"
        )


class ColumnMetadata(BaseModel):
    description: Optional[str] = None
    statistics: Optional[ColumnStatistics] = None
    synonyms: Optional[List[str]] = None
    pii: bool = False


class ColumnContract(BaseModel):
    name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False

    model_config = ConfigDict(extra="ignore", frozen=True)


class ForeignKeyContract(BaseModel):
    constrained_columns: List[str]
    referred_table: TableRef
    referred_columns: List[str]
    cardinality: Literal[
        "one-to-one",
        "one-to-many",
        "many-to-one",
        "many-to-many",
        "unknown",
    ] = "unknown"
    business_meaning: Optional[str] = None

    model_config = ConfigDict(extra="ignore", frozen=True)


class TableContract(BaseModel):
    table: TableRef
    columns: Dict[str, ColumnContract] = Field(default_factory=dict)
    foreign_keys: List[ForeignKeyContract] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", frozen=True)

    @property
    def full_name(self) -> str:
        return f"[{self.table.schema_name}].[{self.table.table_name}]"


class TableMetadata(BaseModel):
    table: TableRef
    columns: Dict[str, ColumnMetadata] = Field(default_factory=dict)
    row_count: Optional[int] = None
    description: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"[{self.table.schema_name}].[{self.table.table_name}]"


class SchemaContract(BaseModel):
    datasource_id: str
    engine_type: str
    tables: Dict[str, TableContract] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore", frozen=True)


class SchemaMetadata(BaseModel):
    datasource_id: str
    engine_type: str
    description: Optional[str] = None
    domains: Optional[List[str]] = None
    tables: Dict[str, TableMetadata] = Field(default_factory=dict)


class SchemaSnapshot(BaseModel):
    contract: SchemaContract
    metadata: SchemaMetadata
