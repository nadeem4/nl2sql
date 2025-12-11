from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, ConfigDict

class CandidateInfo(BaseModel):
    """
    Represents a routing candidate (datasource).

    Attributes:
        id: The datasource ID.
        score: The retrieval score (e.g., vector distance).
    """
    model_config = ConfigDict(extra="ignore")
    id: str
    score: float


class RoutingInfo(BaseModel):
    """
    Detailed routing information for a specific target datasource.

    Attributes:
        layer: The routing layer that made the decision (e.g., "layer_1", "layer_2", "l3_fallback").
        score: The confidence score or distance metric.
        l1_score: The Layer 1 vector distance (if applicable).
        candidates: List of top candidates considered.
        latency: Time taken for routing decision.
        reasoning: Explanation for the decision.
        tokens: Token usage during routing.
    """
    model_config = ConfigDict(extra="ignore")
    layer: str
    score: float
    l1_score: float
    candidates: List[CandidateInfo]
    latency: float
    reasoning: str
    tokens: int
