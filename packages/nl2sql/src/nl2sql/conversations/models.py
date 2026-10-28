"""A conversation: stored turns, each a bounded loop of tool calls (docs/architecture/wave_c.md, section 3).

These are the shapes only; ``engine.conversations`` arrives with C1. A conversation is stored data
(D100): every call names it. A step holds a ``QueryResult`` that is stored without rows (D106).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import Field, model_validator

from nl2sql.api.query_api import QueryResult
from nl2sql.clarify.models import ClarificationRequest
from nl2sql.common.strict import StrictModel
from nl2sql.services.callbacks.token_handler import QuestionUsage

TurnKind = Literal["message", "choice", "retry", "tool"]
TurnStatus = Literal["answered", "needs_clarification", "needs_approval", "stopped", "error"]
StepStatus = Literal["ok", "needs_clarification", "needs_approval", "stopped", "error"]


class ToolCall(StrictModel):
    name: str  # "ask" or an enabled chat tool
    args: Dict[str, Any] = Field(default_factory=dict)  # validated by that tool's args_model


class ToolOutput(StrictModel):
    """What a chat tool returns. Defined here so a stored step can hold one without this module importing
    ``nl2sql.chat_tools`` (which imports ``Conversation``); tool authors import it from ``nl2sql.chat_tools``."""

    kind: Literal["chart", "mermaid", "markdown", "json"]
    title: str
    body: Union[str, Dict[str, Any]]  # chart: ChartSpec as JSON; mermaid and markdown: text
    refs: Dict[str, str] = Field(default_factory=dict)  # e.g. {"dashboard_id": ...}


class ToolStep(StrictModel):
    """One tool call inside a turn."""

    call: ToolCall
    status: StepStatus
    question: Optional[str] = None  # ask: what ran
    trace_id: Optional[str] = None  # ask: the run's id: its events, trace and result pages
    result: Optional[QueryResult] = None  # ask: stored without rows
    output: Optional[ToolOutput] = None  # a chat tool's output
    error: Optional[Dict[str, Any]] = None  # code and message


class ModelStep(StrictModel):
    """The conversation agent's structured output: exactly one of a reply and a call. The loop checks the tool
    name and its args; this check needs no context, so ``structured()``'s parse retry covers it."""

    reply: Optional[str] = None  # the model's message to the user
    call: Optional[ToolCall] = None  # or a tool to call
    offers: List[ToolCall] = Field(default_factory=list)  # with a reply: tools it suggests, run only on a click

    @model_validator(mode="after")
    def _one_of(self) -> "ModelStep":
        if (self.reply is None) == (self.call is None):
            raise ValueError("A model step is a reply or a tool call: exactly one.")
        if self.offers and self.reply is None:
            raise ValueError("Offers go with a reply.")
        return self


class Turn(StrictModel):
    index: int  # 1-based, in the order asked
    at: datetime  # UTC
    kind: TurnKind
    text: str  # what the user typed, or the chosen option's label
    reply: Optional[str] = None  # the model's closing message; None for a choice, a retry or a tool turn
    offers: List[ToolCall] = Field(default_factory=list)  # tools the model suggests; a client runs one on a click
    steps: List[ToolStep]  # in the order called
    status: TurnStatus
    trace_id: Optional[str] = None  # the turn's own scope: the loop's model calls
    usage: QuestionUsage = Field(default_factory=QuestionUsage)  # the loop's model calls


class Conversation(StrictModel):
    id: str  # random, 22 url-safe characters
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None  # the owner (D105)
    title: str  # the first message, cut at 80 characters
    datasource_id: Optional[str] = None  # when set, every ask skips routing
    dashboard_id: Optional[str] = None  # the dashboard the dashboard tools edit
    created_at: datetime
    updated_at: datetime
    turns: List[Turn] = Field(default_factory=list)
    pending: Optional[ClarificationRequest] = None  # Wave B's request the last turn ended with


class ConversationInfo(StrictModel):
    id: str
    title: str
    updated_at: datetime
    turns: int
