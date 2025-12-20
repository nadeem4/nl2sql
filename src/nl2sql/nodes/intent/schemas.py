from enum import Enum
from typing import List, Dict, Literal
from pydantic import BaseModel, Field


class EntityRole(str, Enum):
    FACT = "fact"
    STATE = "state"
    REFERENCE = "reference"


class TimeScope(str, Enum):
    CURRENT_STATE = "current_state"
    POINT_IN_TIME = "point_in_time"
    RANGE = "range"
    ALL_TIME = "all_time"



class EntityGroup(BaseModel):
    role: EntityRole
    entity_ids: List[str]

class Entity(BaseModel):
    entity_id: str
    name: str
    role: EntityRole
    required_attributes: List[str] = Field(default_factory=list)


class IntentResponse(BaseModel):
    canonical_query: str = Field(description="Canonical, database-centric rewrite of the user query.")

    response_type: Literal["tabular", "kpi", "summary"]

    analysis_intent: Literal[
        "lookup",
        "aggregation",
        "comparison",
        "trend",
        "diagnostic",
        "validation"
    ]

    time_scope: TimeScope

    keywords: List[str] = Field(default_factory=list)

    entities: List[Entity]

    entity_roles: List[EntityGroup]

    synonyms: List[str] = Field(default_factory=list)

    ambiguity_level: Literal["low", "medium", "high"]
