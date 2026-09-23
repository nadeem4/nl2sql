from typing import List, Dict, Any
from pydantic import BaseModel, Field


class AggregatorResponse(BaseModel):
    terminal_results: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    computed_artifacts: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Any] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
