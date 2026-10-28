"""Conversations in the state store: ownership, and the rule that a stored result has no rows (D105, D106)."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from nl2sql.api.query_api import QueryResult
from nl2sql.common.errors import ConversationNotFound
from nl2sql.conversations.models import Conversation, ConversationInfo

Owner = Tuple[Optional[str], Optional[str]]  # (tenant_id, user_id)


def owner_of(user: Any, default_tenant: Optional[str]) -> Owner:
    """The pair a conversation belongs to: the caller's tenant (else the engine's) and user."""
    return (getattr(user, "tenant_id", None) or default_tenant, getattr(user, "user_id", None))


def strip_rows(result: QueryResult) -> QueryResult:
    """``result`` as a stored step keeps it: everything but the rows of its sub-queries (D106)."""
    return result.model_copy(update={"sub_queries": [sq.model_copy(update={"rows": None})
                                                      for sq in result.sub_queries]})


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
