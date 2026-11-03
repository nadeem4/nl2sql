"""Request bodies of the saved-answers routes. Strict: an unknown
field is a 422. Responses are the engine's own models (``SaveReport``, ``SavedAnswer``, ``QueryResult``)."""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from nl2sql import StepRef


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveBody(_Body):
    trace_id: str
    conversation_id: str
    step: StepRef  # {turn?, step}, both 1-based; without turn, the latest
    note: Optional[str] = None


class RunAnswerBody(_Body):
    approval_ids: List[str] = []  # request ids of granted approvals
