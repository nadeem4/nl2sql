"""``engine.answers``: a person's saved answers.

The logic is ``nl2sql.answers.service``. Every method takes the caller keyword-only and required: a saved answer is
its owner's alone, an admin's read included, and another caller's is ``AnswerNotFound``.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Union

from nl2sql.answers.models import SavedAnswer, SaveReport
from nl2sql.answers.service import Answers
from nl2sql.api.query_api import QueryResult
from nl2sql.dashboards.models import StepRef
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


class AnswersAPI:
    """``engine.answers``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._service = Answers(ctx)

    def save(self, *, trace_id: str, conversation_id: str, step: StepRef, user_context: UserContext,
             note: Optional[str] = None) -> SaveReport:
        """Keeps the answer of an ``ask`` step of the caller's conversation, and rates its run up.

        The plan comes from the stored step (the helper ``AddTile`` uses), never from model text. The run is
        rated up through ``engine.feedback``, which also files its ``verified_query`` proposal when it can; the
        answer is saved either way, and the report says why no proposal was filed. Saving the same run
        again is the same answer and the same proposal.

        Returns:
            The ``SaveReport``: ``answer``, ``proposed``, ``proposal_id`` and ``not_proposed_reason``.

        Raises:
            ConversationNotFound: no such conversation for this caller.
            NotSavable: the step did not answer, kept no plan, or ``trace_id`` is not its run.
            AnswerLimitReached: the caller holds ``answers.max_per_user`` answers already.
            TypeError: ``user_context`` is not a ``UserContext`` with a ``user_id``.
        """
        return self._service.save(trace_id=trace_id, conversation_id=conversation_id, step=step,
                                  user_context=user_context, note=note)

    def list(self, *, user_context: UserContext, datasource_id: Optional[str] = None) -> List[SavedAnswer]:
        """The caller's saved answers, newest first; only those of ``datasource_id`` when given."""
        return self._service.list(user_context=user_context, datasource_id=datasource_id)

    def get(self, answer_id: str, *, user_context: UserContext) -> SavedAnswer:
        """One of the caller's saved answers.

        Raises:
            AnswerNotFound: it does not exist or is not the caller's.
        """
        return self._service.get(answer_id, user_context=user_context)

    def delete(self, answer_id: str, *, user_context: UserContext) -> None:
        """Deletes one of the caller's saved answers (``AnswerNotFound`` as for ``get``)."""
        self._service.delete(answer_id, user_context=user_context)

    def run(self, answer_id: str, *, user_context: UserContext, llm_override: Any = None,
            approvals: Optional[Sequence[Union[Approval, str]]] = None) -> QueryResult:
        """Runs the saved plan again as the caller: scope, hooks, validator and approvals apply, as for any run.

        A plan the schema has outgrown comes back as a result with status ``error`` whose first error has
        ``error_code`` ``TILE_NEEDS_FIXING``, as a dashboard tile reports it; the answer stays, and its owner asks
        again. ``approvals`` are ``Approval`` objects or request ids, as ``run_query`` takes them.

        Raises:
            AnswerNotFound: it does not exist or is not the caller's.
        """
        return self._service.run(answer_id, user_context=user_context, llm_override=llm_override,
                                 approvals=approvals)
