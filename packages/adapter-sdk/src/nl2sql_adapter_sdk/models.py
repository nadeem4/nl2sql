from typing import List, Any, Dict, Optional
from pydantic import BaseModel

class Column(BaseModel):
    name: str
    type: str
    is_nullable: bool = True
    is_primary_key: bool = False

class Table(BaseModel):
    name: str
    schema_name: Optional[str] = None
    columns: List[Column]

class SchemaMetadata(BaseModel):
    datasource_id: str
    tables: List[Table]

class QueryResult(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    raw: Optional[Any] = None  # engine-native result for debugging

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

class ExecutionMetrics(BaseModel):
    execution_ms: float
    rows_returned: int
    retries: int = 0
    engine: str
    extra: Dict[str, Any] = {}

class AdapterError(BaseModel):
    code: str
    message: str
    retriable: bool
    raw: Optional[Any] = None
