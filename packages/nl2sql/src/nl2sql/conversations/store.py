"""Conversations in the state store: ownership, and the rule that a stored result has no rows (D105, D106)."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from nl2sql.api.query_api import QueryResult
from nl2sql.common.errors import ConversationNotFound
from nl2sql.conversations.models import Conversation, ConversationInfo, ToolStep

Owner = Tuple[Optional[str], Optional[str]]  # (tenant_id, user_id)


def owner_of(user: Any, default_tenant: Optional[str]) -> Owner:
    """The pair a conversation belongs to: the caller's tenant (else the engine's) and user.

    Raises:
        TypeError: no caller, or a caller with no ``user_id``. Failing open would make a shared anonymous owner.
    """
    user_id = getattr(user, "user_id", None)
    if not user_id:
        raise TypeError("user_context is required: pass the caller (a UserContext with a user_id).")
    return (getattr(user, "tenant_id", None) or default_tenant, user_id)


def strip_rows(result: QueryResult) -> QueryResult:
    """``result`` as a stored step keeps it: everything but the rows of its sub-queries (D106)."""
    return result.model_copy(update={"sub_queries": [sq.model_copy(update={"rows": None})
                                                      for sq in result.sub_queries]})


def strip_output_rows(step: ToolStep) -> ToolStep:
    """``step`` as it is stored: a chart output keeps its spec and columns but not its rows (D106); a client draws
    the chart again through ``engine.results.chart``."""
    output = step.output
    if output is None or not isinstance(output.body, dict) or not isinstance(output.body.get("data"), dict):
        return step
    body = {**output.body, "data": {**output.body["data"], "rows": []}}
    return step.model_copy(update={"output": output.model_copy(update={"body": body})})


class ConversationStore:
    """The state store's conversation documents as models. A conversation is another owner's: not found."""

    def __init__(self, state: Any) -> None:
        self._state = state

    def get(self, conversation_id: str, owner: Owner) -> Conversation:
        doc = self._state.get_conversation(conversation_id)
        if doc is None or (doc.get("tenant_id"), doc.get("user_id")) != owner:
            raise ConversationNotFound(f"No conversation '{conversation_id}' for this caller.")
        return Conversation.model_validate(doc)

    def put(self, conversation: Conversation) -> None:
        self._state.put_conversation(conversation.model_dump(mode="json"))

    def list(self, owner: Owner, limit: int) -> List[ConversationInfo]:
        return [ConversationInfo(id=doc["id"], title=doc["title"], updated_at=doc["updated_at"],
                                 turns=len(doc.get("turns") or []))
                for doc in self._state.list_conversations(owner[0], owner[1], limit)]

    def delete(self, conversation_id: str) -> None:
        self._state.delete_conversation(conversation_id)
