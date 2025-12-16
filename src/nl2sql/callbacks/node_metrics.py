from dataclasses import dataclass
from typing import Optional


@dataclass
class NodeMetrics:
    start_time: float = float("inf")
    end_time: float = 0.0
    duration: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None
    datasource_id: Optional[str] = None
