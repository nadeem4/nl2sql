from typing import List, Any, Dict, Optional
from pydantic import BaseModel

class ColumnStatistics(BaseModel):
    null_percentage: float
    distinct_count: int
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    sample_values: List[Any] = []

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

class CapabilitySet(BaseModel):
    supports_cte: bool = True
    supports_window_functions: bool = True
    supports_limit_offset: bool = True
    supports_multi_db_join: bool = False
    supports_vector: bool = False
    supports_dry_run: bool = False

class AdapterError(BaseModel):
    code: str
    message: str
    retriable: bool
    raw: Optional[Any] = None
