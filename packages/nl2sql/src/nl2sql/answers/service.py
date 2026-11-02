"""Saving, listing, deleting and re-running a person's answers (docs/architecture/wave_d.md, section 18).

A saved answer is a typed plan, not SQL and not rows. Re-running it is a normal ``run_query`` of that plan as the
viewer (D240): the scope, the hooks, the validator and the approvals all apply, and nothing is shared across users.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Union

from nl2sql.answers.models import SavedAnswer, SaveReport
from nl2sql.answers.store import AnswerStore
from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.common.errors import AnswerLimitReached, ErrorCode, InvalidArgument, NotSavable, TraceNotFound
from nl2sql.conversations.store import ConversationStore, Owner, owner_of
from nl2sql.dashboards.edits import ask_step, plan_from_step
from nl2sql.dashboards.filters import supply
from nl2sql.dashboards.models import StepRef
from nl2sql.dashboards.run import _classify
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext

_VALIDATOR = "logical_validator"


class Answers:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = AnswerStore(ctx.state)

    def _owner(self, user_context: UserContext) -> Owner:
        return owner_of(user_context, self._ctx.tenant_id)

    def save(self, *, trace_id: str, conversation_id: str, step: StepRef, user_context: UserContext,
             note: Optional[str] = None) -> SaveReport:
        owner = self._owner(user_context)
        conversation = ConversationStore(self._ctx.state).get(conversation_id, owner)
        try:
            found = ask_step(conversation, step)
            question, plan = plan_from_step(conversation, step)
        except InvalidArgument as e:
            raise NotSavable(str(e)) from e
        if found.trace_id != trace_id:
            raise NotSavable(f"Run '{trace_id}' is not the run of that step ({found.trace_id}).")
        mine = self._store.list(owner)
        answer = next((a for a in mine if a.trace_id == trace_id), None)  # saving twice keeps one answer
        cap = self._ctx.config.answers.max_per_user
        if answer is None and len(mine) >= cap:
            raise AnswerLimitReached(f"You already keep {cap} saved answers (answers.max_per_user).",
                                     fix="Delete one to save another.")
        proposal, reason = None, ""
        try:
            from nl2sql.api.feedback_api import FeedbackAPI

            _, proposal, reason = FeedbackAPI(self._ctx)._record(trace_id, "up", user_context=user_context, note=note)
        except TraceNotFound:  # the run's trace was not kept (settings.traces.mode): nothing to rate or propose
            reason = "no_trace"
        if answer is None:
            datasource_id = plan.verified_datasource_id or plan.decomposition["sub_queries"][0]["datasource_id"]
            answer = SavedAnswer(id=secrets.token_urlsafe(12), tenant_id=owner[0], user_id=owner[1],
                                 question=question, datasource_id=datasource_id, plan=plan,
                                 created_at=datetime.now(timezone.utc), trace_id=trace_id)
            self._store.put(answer)
        return SaveReport(answer=answer, proposed=proposal is not None,
                          proposal_id=proposal.id if proposal is not None else None,
                          not_proposed_reason=None if proposal is not None else (reason or "not_filed"))

    def list(self, *, user_context: UserContext, datasource_id: Optional[str] = None) -> List[SavedAnswer]:
        return [a for a in self._store.list(self._owner(user_context))
                if datasource_id is None or a.datasource_id == datasource_id]

    def get(self, answer_id: str, *, user_context: UserContext) -> SavedAnswer:
        return self._store.get(answer_id, self._owner(user_context))

    def delete(self, answer_id: str, *, user_context: UserContext) -> None:
        self._store.delete(answer_id, self._owner(user_context))

    def run(self, answer_id: str, *, user_context: UserContext, llm_override: Any = None,
            approvals: Optional[Sequence[Union[Approval, str]]] = None) -> QueryResult:
        answer = self.get(answer_id, user_context=user_context)
        try:
            supplied, _ = supply(answer.plan, ())
            result = QueryAPI(self._ctx).run_query(answer.question, plan=supplied, user_context=user_context,
                                                   llm_override=llm_override,
                                                   approvals=list(approvals) if approvals else None)
        except ValueError as error:  # the stored plan does not parse, or run_query refuses it at the call
            return QueryResult(status="error", errors=[_needs_fixing(str(error))])
        status, error = _classify(result)
        if status == "needs_fixing":  # the schema has outgrown the plan: its owner asks again (D240)
            result = result.model_copy(update={"errors": [_needs_fixing((error or {}).get("message", "")),
                                                          *result.errors]})
        return result


def _needs_fixing(message: str) -> Dict[str, Any]:
    return {"error_code": ErrorCode.TILE_NEEDS_FIXING.value, "node": _VALIDATOR, "message": message}
