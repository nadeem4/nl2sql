from typing import List, Any, Dict, Optional, Union
from pydantic import BaseModel

Scalar = Union[str, int, float, bool, None]
JsonValue = Union[Scalar, List[Scalar], Dict[str, Scalar]]

class ColumnStatistics(BaseModel):
    null_percentage: float
    distinct_count: int
    min_value: Optional[Scalar] = None
    max_value: Optional[Scalar] = None
    sample_values: List[JsonValue] = []

class Column(BaseModel):
    name: str
    type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    description: Optional[str] = None
    statistics: Optional[ColumnStatistics] = None

class ForeignKey(BaseModel):
    constrained_columns: List[str]
    referred_table: str
    referred_columns: List[str]
    referred_schema: Optional[str] = None

class Table(BaseModel):
    
    name: str
    alias: Optional[str] = None
    schema_name: Optional[str] = None
    row_count: Optional[int] = None
    columns: List[Column]
    foreign_keys: List[ForeignKey] = []
    description: Optional[str] = None

class SchemaMetadata(BaseModel):
    datasource_id: str
    datasource_engine_type: str
    tables: List[Table]

class QueryResult(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    raw: Optional[Any] = None
    execution_time_ms: Optional[float] = None
    bytes_returned: Optional[int] = None

class DryRunResult(BaseModel):
    is_valid: bool
    error_message: Optional[str] = None
    data: Optional[Any] = None

class QueryPlan(BaseModel):
    plan_text: str
    format: str = "text" # or 'json', 'xml'

class CostEstimate(BaseModel):
    estimated_cost: float
    estimated_rows: int
    estimated_time_ms: Optional[float] = None

class AdapterError(BaseModel):
    code: str
    message: str
    retriable: bool
    raw: Optional[Any] = None
