"""Request bodies of the conversation routes.

Responses are the facade's own models (``Conversation``, ``ConversationInfo``, ``Turn``, ``ChatToolInfo``), served as
they are. A body is strict: a field it does not name is a 422.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(_Body):
    datasource_id: Optional[str] = Field(None, description="Every question of the conversation skips routing and asks "
                                                           "this datasource.")
    dashboard_id: Optional[str] = Field(None, description="Opens the chat on this dashboard (the caller's, draft or "
                                                          "saved), so the model can edit it.")


class TurnRequest(_Body):
    text: Optional[str] = Field(None, description="What the user typed.")
    choice: Optional[str] = Field(None, description="An option id of the pending clarification, sent instead of "
                                                    "`text`; the question is asked again with no model call.")
    execute: bool = Field(True, description="`false` plans and validates without running SQL.")

    @model_validator(mode="after")
    def _text_or_choice(self) -> "TurnRequest":
        if (self.text is None) == (self.choice is None):
            raise ValueError("Send the message as `text` or a `choice`, one of the two.")
        return self


class RetryRequest(_Body):
    approval_ids: List[str] = Field(default_factory=list, description="Request ids of granted approvals "
                                                                       "(`POST /approvals/{id}/grant`).")


class ToolRequest(_Body):
    args: Dict[str, Any] = Field(default_factory=dict, description="The tool's arguments, as `GET /chat-tools` "
                                                                    "describes them.")
