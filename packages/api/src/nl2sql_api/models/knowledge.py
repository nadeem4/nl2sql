from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql import ClarificationChoice


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceRequest(_Strict):
    """``POST /knowledge/choices``: a clarification pick, filed as a `term` proposal."""

    choice: ClarificationChoice
    term: Optional[str] = None


class AcceptRequest(_Strict):
    """``POST /proposals/accept``: ids, and per id the fields to merge over its change before it is written."""

    ids: List[str] = Field(min_length=1)
    edits: Optional[Dict[str, Dict[str, Any]]] = None


class RejectRequest(_Strict):
    ids: List[str] = Field(min_length=1)
    reason: Optional[str] = None


class ExamplesRequest(_Strict):
    """``POST /proposals/examples``: starter questions to file as `example` proposals."""

    datasource_id: str
    questions: List[str] = Field(min_length=1)
