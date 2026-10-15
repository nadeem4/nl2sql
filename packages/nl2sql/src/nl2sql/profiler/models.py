"""What ``engine.knowledge.profile`` returns, one per datasource profiled."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ProfileReport(BaseModel):
    """One datasource's profile: the proposals it filed and how far its bounds let it go."""

    datasource_id: str
    schema_version: str
    proposals: Dict[str, int] = Field(default_factory=dict)  # created, updated or superseded, by kind
    probes: int = 0  # overlap probes run
    skipped_by_budget: int = 0  # join candidates proposed without a probe
    drafted: int = 0  # descriptions the model drafted (draft=True only)
    cost: Optional[float] = None  # the drafting calls' cost
    seconds: float
    stopped_by: Literal["done", "time_budget", "max_probes", "cost_cap"] = "done"
    issues: List[str] = Field(default_factory=list)  # probes that failed, a draft call that failed
