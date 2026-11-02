"""Saved answers in the state store: owner-only, the models behind the JSON documents (D238, D247).

An answer is its owner's, a ``(tenant_id, user_id)`` pair as for a conversation; another caller's is "not found",
never "forbidden", and an admin is another caller.
"""
from __future__ import annotations

from typing import Any, List

from nl2sql.answers.models import SavedAnswer
from nl2sql.common.errors import AnswerNotFound
from nl2sql.conversations.store import Owner


class AnswerStore:
    def __init__(self, state: Any) -> None:
        self._state = state

    def put(self, answer: SavedAnswer) -> None:
        self._state.put_saved_answer(answer.model_dump(mode="json"))

    def get(self, answer_id: str, owner: Owner) -> SavedAnswer:
        doc = self._state.get_saved_answer(answer_id)
        if doc is None or (doc.get("tenant_id"), doc.get("user_id")) != owner:
            raise AnswerNotFound(f"No saved answer '{answer_id}' for this caller.")
        return SavedAnswer.model_validate(doc)

    def list(self, owner: Owner) -> List[SavedAnswer]:
        return [SavedAnswer.model_validate(d) for d in self._state.list_saved_answers(owner[0], owner[1])]

    def delete(self, answer_id: str, owner: Owner) -> None:
        self.get(answer_id, owner)  # another owner's is not found, and deletes nothing
        self._state.delete_saved_answer(answer_id)
