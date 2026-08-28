from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field



class Column(BaseModel):
    """Lightweight column schema for routing/planning."""

    name: str
    type: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class Table(BaseModel):
    """Lightweight table schema for routing/planning."""

    name: str
    columns: List[Column] = Field(default_factory=list)
    description: Optional[str] = None
    primary_key: Optional[List[str]] = None
    foreign_keys: Optional[Dict[str, List[str]]] = None

    model_config = ConfigDict(extra="allow")