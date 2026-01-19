from __future__ import annotations
from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field


class BaseChunk(BaseModel):
    """
    Base class for all schema chunks.
    Chunk IDs MUST be deterministic and stable across runs.
    """
    id: str
    type: str

    def get_page_content(self) -> str:
        raise NotImplementedError("Each chunk must implement get_page_content()")

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
        }

class TableRef(BaseModel):
    schema_name: str
    table_name: str

    @property
    def full_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class ColumnRef(BaseModel):
    schema_name: str
    table_name: str
    column_name: str

    @property
    def full_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column_name}"


class DatasourceChunk(BaseChunk):
    type: Literal["schema.datasource"] = Field(
        default="schema.datasource", frozen=True
    )

    datasource_id: str
    description: str
    domains: Optional[List[str]] = None
    schema_version: str
    examples: Optional[List[str]] = None

    def get_page_content(self) -> str:
        domains = ", ".join(self.domains) if self.domains else "N/A"
        examples = ", ".join(self.examples) if self.examples else "N/A"
        return (
            f"Datasource: {self.datasource_id}\n"
            f"{self.description}\n"
            f"Domains: {domains}\n"
            f"Examples: {examples}"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            **super().get_metadata(),
            "datasource_id": self.datasource_id,
            "schema_version": self.schema_version,
        }


class TableChunk(BaseChunk):
    type: Literal["schema.table"] = Field(
        default="schema.table", frozen=True
    )

    datasource_id: str
    table: TableRef
    description: Optional[str] = None
    primary_key: List[str] = Field(default_factory=list)
    foreign_keys: List[str] = Field(
        default_factory=list,
        description="Human-readable FK summaries (retrieval only)"
    )
    row_count: Optional[int] = None
    schema_version: str

    def get_page_content(self) -> str:
        pk = ", ".join(self.primary_key) if self.primary_key else "None"
        return (
            f"Table: {self.table.full_name}\n"
            f"{self.description or ''}\n"
            f"Primary Key: {pk}"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            **super().get_metadata(),
            "datasource_id": self.datasource_id,
            "table": self.table.full_name,
            "row_count": self.row_count,
            "schema_version": self.schema_version,
        }


class ColumnChunk(BaseChunk):
    type: Literal["schema.column"] = Field(
        default="schema.column", frozen=True
    )

    datasource_id: str
    column: ColumnRef
    dtype: str
    description: Optional[str] = None
    column_stats: Dict[str, Any] = Field(default_factory=dict)
    synonyms: Optional[List[str]] = None
    pii: bool = False
    schema_version: str

    def get_page_content(self) -> str:
        stats = f"Stats: {self.column_stats}" if self.column_stats else ""
        return (
            f"Column: {self.column.full_name}\n"
            f"Type: {self.dtype}\n"
            f"{self.description or ''}\n"
            f"{stats}"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            **super().get_metadata(),
            "datasource_id": self.datasource_id,
            "column": self.column.full_name,
            "dtype": self.dtype,
            "pii": self.pii,
            "schema_version": self.schema_version,
        }

class RelationshipChunk(BaseChunk):
    type: Literal["schema.relationship"] = Field(
        default="schema.relationship", frozen=True
    )

    datasource_id: str
    from_table: TableRef
    to_table: TableRef

    from_columns: Optional[List[str]] = None
    to_columns: Optional[List[str]] = None

    cardinality: Literal[
        "one-to-one",
        "one-to-many",
        "many-to-one",
        "many-to-many",
        "unknown"
    ] = "unknown"

    business_meaning: Optional[str] = None
    schema_version: str

    def get_page_content(self) -> str:
        return (
            f"Relationship between {self.from_table.full_name} "
            f"and {self.to_table.full_name}.\n"
            f"{self.business_meaning or ''}"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            **super().get_metadata(),
            "datasource_id": self.datasource_id,
            "from_table": self.from_table.full_name,
            "to_table": self.to_table.full_name,
            "cardinality": self.cardinality,
            "schema_version": self.schema_version,
        }


class MetricChunk(BaseChunk):
    type: Literal["schema.metric"] = Field(
        default="schema.metric", frozen=True
    )

    datasource_id: str
    name: str
    definition: Optional[str] = None
    grain: Optional[str] = None
    business_meaning: Optional[str] = None
    owner: Optional[str] = None
    version: str = "v1"
    schema_version: str

    def get_page_content(self) -> str:
        return (
            f"Metric: {self.name}\n"
            f"{self.business_meaning or ''}\n"
            f"Definition: {self.definition or ''}\n"
            f"Grain: {self.grain or 'N/A'}"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            **super().get_metadata(),
            "datasource_id": self.datasource_id,
            "name": self.name,
            "version": self.version,
            "schema_version": self.schema_version,
        }
