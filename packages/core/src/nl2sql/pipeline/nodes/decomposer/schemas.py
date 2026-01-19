from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, Field
from nl2sql.datasources import QueryResult as ExecutionResult
from nl2sql_sqlalchemy_adapter.schema.models import Table


class ColumnDefinition(BaseModel):
    name: str
    type: str

class SubQuery(BaseModel):
    id: str
    query: str
    datasource_id: str
    complexity: Literal["simple", "complex"]
    relevant_tables: Optional[List[Table]] = None

class DecomposerResponse(BaseModel):
    sub_queries: List[SubQuery]
    confidence: float
    output_mode: Literal["data", "synthesis"]
    reasoning: str
