"""``engine.conversations``: a model-led conversation over the database (docs/architecture/wave_c.md, section 3).

The logic is ``nl2sql.conversations.api``. Every
method takes the caller keyword-only and required (D105, D130), so an app that forgot it fails closed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence

from nl2sql.conversations.api import Conversations
from nl2sql.conversations.models import Conversation, ConversationInfo, Turn
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql.api.query_api import QueryResult
    from nl2sql.public_api import NL2SQL
    from nl2sql.common.cancellation import CancellationToken
    from nl2sql.hooks.approval import Approval
    from nl2sql_sdk.events import Event


class ConversationsAPI:
    """``engine.conversations``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime, engine: "NL2SQL"):
        self._ctx = ctx
        self._service = Conversations(ctx, engine)  # a chat tool is handed the engine, the public facade

    def start(self, *, user_context: UserContext, datasource_id: Optional[str] = None,
              dashboard_id: Optional[str] = None) -> Conversation:
        """A new, empty conversation owned by the caller; with ``dashboard_id``, one that edits that saved
        dashboard. Nothing runs."""
        return self._service.start(user_context=user_context, datasource_id=datasource_id, dashboard_id=dashboard_id)

    def ask(self, conversation_id: str, text: Optional[str] = None, *, user_context: UserContext,
            choice: Optional[str] = None, execute: bool = True,
            cancellation_token: Optional["CancellationToken"] = None) -> Turn:
        """One turn: the model's loop, stored. ``choice`` is an option id of the pending clarification, given
        instead of ``text``; it re-asks with no model call."""
        return self._service.ask(conversation_id, text, user_context=user_context, choice=choice, execute=execute,
                                  cancellation_token=cancellation_token)

    def stream(self, conversation_id: str, text: Optional[str] = None, *, user_context: UserContext,
               choice: Optional[str] = None, execute: bool = True) -> Iterator["Event"]:
        """As ``ask``, yielding the caller-safe run events of each ``ask`` step, with ``conversation.step``
        after each step and ``conversation.turn`` as the terminal event."""
        return self._service.stream(conversation_id, text, user_context=user_context, choice=choice, execute=execute)

    def retry(self, conversation_id: str, *, user_context: UserContext,
              approvals: Sequence["Approval"] = ()) -> Turn:
        """Re-runs the last ``ask`` step's question as a new turn of kind ``retry``, with no model call."""
        return self._service.retry(conversation_id, user_context=user_context, approvals=approvals)

    def tool(self, conversation_id: str, name: str, args: Optional[Dict[str, Any]] = None, *,
             user_context: UserContext) -> Turn:
        """Calls a registry chat tool by name as a turn of kind ``tool``, with no model call."""
        return self._service.tool(conversation_id, name, args, user_context=user_context)

    def ask_only(self, conversation_id: str, question: str, *, user_context: UserContext,
                 choice: Optional[str] = None) -> "QueryResult":
        """Runs only the ``ask`` tool, stored as a turn of kind ``tool``, with no model call of its own."""
        return self._service.ask_only(conversation_id, question, user_context=user_context, choice=choice)

    def report(self, conversation_id: str, *, user_context: UserContext, turns: Optional[List[int]] = None,
               title: Optional[str] = None) -> str:
        """The Markdown report of the conversation's answered turns, with no model call."""
        return self._service.report(conversation_id, user_context=user_context, turns=turns, title=title)

    def get(self, conversation_id: str, *, user_context: UserContext) -> Conversation:
        """One conversation, with its turns.

        Raises:
            ConversationNotFound: it does not exist or is not the caller's.
        """
        return self._service.get(conversation_id, user_context=user_context)

    def list(self, *, user_context: UserContext, limit: int = 20) -> List[ConversationInfo]:
        """The caller's conversations, newest first."""
        return self._service.list(user_context=user_context, limit=limit)

    def delete(self, conversation_id: str, *, user_context: UserContext) -> None:
        """Deletes it and its turns; its results stay until their own retention."""
        self._service.delete(conversation_id, user_context=user_context)
