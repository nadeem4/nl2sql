from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class QueryRequest(BaseModel):
    natural_language: str
    datasource_id: Optional[str] = None
    execute: bool = True
    user_context: Optional[Dict[str, Any]] = None


class QueryResponse(BaseModel):
    sql: Optional[str] = None
    results: list = []
    final_answer: Optional[str] = None
    errors: list = []
    trace_id: Optional[str] = None
    reasoning: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []