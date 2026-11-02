from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class InspectRequest(BaseModel):
    """``POST /index/inspect``: one retrieval run. Anything left out takes the engine's default."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    k: int = Field(8, ge=1)
    lambda_mult: Optional[float] = None
    types: Optional[List[str]] = None
    datasource_id: Optional[str] = None
