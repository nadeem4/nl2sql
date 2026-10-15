"""The pinned-plan entry: a run whose decomposition and plans are given, not routed and planned.

Three callers pin a plan (docs/architecture/wave_a.md, section 7):

- ``supplied``: ``run_query(plan=SuppliedPlan(...))``, a plan the caller holds.
- ``approved``: the re-run of a question with an ``Approval``; the request's
  decomposition and plan are pinned.
- ``chosen``: Wave B's clarification re-ask, a plan the asker picked.

A pinned run skips the curated matcher, the datasource resolver, the
decomposer and the planner's model call. ``PinnedEntryNode`` checks every
datasource of the decomposition against the caller's scope, as the resolver
would; the planner returns the pinned plan through ``plan.after``, so the
hooks, the logical validator and the generator see it like any other plan. A
pinned plan is never cached, and a validator failure ends its sub-query with
the validator's error: the refiner is never asked to replace it.

A pinned verified query (an approved verified-path request) is not routed
here: the curated matcher runs it with its values, with no matching call.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

from pydantic import BaseModel, Field, model_validator

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.hooks.approval import Approval, VerifiedRef, request_problem
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql_sdk.policy import UserContext

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

logger = get_logger("pinned")


class PinnedSource(str, Enum):
    """Where a pinned plan came from; it is the sub-query's ``plan_source``."""

    SUPPLIED = "supplied"
    APPROVED = "approved"
    CHOSEN = "chosen"


class SuppliedPlan(BaseModel):
    """A plan the caller holds, for ``run_query(plan=...)``: one sub-query of one datasource and its plan.

    The question is still asked (the answer is written for it), but nothing is
    routed, decomposed or planned. The plan passes ``plan.after``, the
    validator and the generator under the caller's scope.
    """

    datasource_id: str
    sub_query: SubQuery
    plan: PlanModel

    @model_validator(mode="after")
    def _one_datasource(self) -> "SuppliedPlan":
        if self.sub_query.datasource_id != self.datasource_id:
            raise ValueError(f"The sub-query is for '{self.sub_query.datasource_id}', "
                             f"not '{self.datasource_id}'.")
        return self


class PinnedRun(BaseModel):
    """What a pinned run takes as given: the decomposition and each pinned sub-query's plan.

    ``plans`` is keyed by sub-query id; a sub-query without one is planned as
    usual. ``verified`` (with ``verified_datasource_id``) pins a verified query
    instead, for the curated matcher.
    """

    source: PinnedSource
    decomposition: Optional[DecomposerResponse] = None
    plans: Dict[str, PlanModel] = Field(default_factory=dict)
    verified: Optional[VerifiedRef] = None
    verified_datasource_id: Optional[str] = None

    @property
    def enters_pinned(self) -> bool:
        """Whether the run enters at ``pinned_entry`` (a pinned decomposition); a verified pin does not."""
        return self.decomposition is not None

    @classmethod
    def supplied(cls, plan: SuppliedPlan) -> "PinnedRun":
        """A one-sub-query run of ``plan``: its sub-query, standalone."""
        decomposition = DecomposerResponse(
            sub_queries=[plan.sub_query],
            combine_groups=[CombineGroup(group_id="supplied", operation="standalone",
                                         inputs=[CombineInput(subquery_id=plan.sub_query.id)])])
        return cls(source=PinnedSource.SUPPLIED, decomposition=decomposition, plans={plan.sub_query.id: plan.plan})


def pinned_from_approvals(approvals: Sequence[Approval], *, question: str,
                          user: Optional[UserContext]) -> Optional[PinnedRun]:
    """The run the approvals pin, or None when none of them is this run's.

    An approval whose request was edited, or is for another question or
    another asker, pins nothing (``request_problem``); the run then plans as
    usual and the hook asks again. The first valid approval decides: its
    verified query, or its decomposition with the plan of every valid approval
    made from that same decomposition. Whether the approver may approve is
    checked at ``execute.before``, against the hook's configured roles.
    """
    valid = []
    for approval in approvals:
        problem = request_problem(approval, question=question, user=user)
        if problem:
            logger.warning(f"An approval pins nothing: {problem}.")
            continue
        valid.append(approval.request)
    if not valid:
        return None
    first = valid[0]
    if first.verified is not None:
        return PinnedRun(source=PinnedSource.APPROVED, verified=first.verified,
                         verified_datasource_id=first.datasource_id)
    if first.decomposition is None:
        return None
    plans = {r.sub_query.id: r.plan for r in valid
             if r.decomposition == first.decomposition and r.sub_query is not None and r.plan is not None}
    return PinnedRun(source=PinnedSource.APPROVED, decomposition=first.decomposition, plans=plans)


class PinnedEntryNode:
    """The graph's entry for a pinned decomposition: the resolver's scope check, then the layer router.

    Each datasource of the decomposition must be one the caller's scope
    reaches; otherwise the run ends with ``SECURITY_VIOLATION`` before any
    sub-query runs. ``datasources.after`` is not called: there is no routing.
    """

    def __init__(self, ctx: Any):
        self.node_name = "pinned_entry"
        self.ctx = ctx

    def __call__(self, state: "GraphState") -> Dict[str, Any]:
        decomposition = state.pinned.decomposition
        known = set(self.ctx.ds_registry.list_ids())
        policy_engine = self.ctx.policy_engine
        for ds_id in sorted({sq.datasource_id for sq in decomposition.sub_queries}):
            if ds_id not in known or not policy_engine.scope(state.user_context, ds_id).reaches_datasource():
                return {"errors": [PipelineError(
                    node=self.node_name, message=f"This role may not read the datasource '{ds_id}'.",
                    severity=ErrorSeverity.CRITICAL, error_code=ErrorCode.SECURITY_VIOLATION)]}
        return {
            "decomposer_response": decomposition,
            "execution_dag": build_execution_dag(decomposition),
            "reasoning": [{"node": self.node_name, "content": (
                f"The decomposition is pinned ({state.pinned.source.value}); "
                f"{len(state.pinned.plans)} of {len(decomposition.sub_queries)} plans are pinned.")}],
        }
