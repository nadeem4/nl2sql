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
    from nl2sql.hooks.runner import HookRunner
    from nl2sql.pipeline.state import GraphState

logger = get_logger("pinned")


class PinnedSource(str, Enum):
    """Where a pinned plan came from; it is the sub-query's ``plan_source``."""

    SUPPLIED = "supplied"
    APPROVED = "approved"
    CHOSEN = "chosen"


class SuppliedPlan(BaseModel):
    """A plan the caller holds, for ``run_query(plan=...)``: a whole run, or a verified query.

    Shaped on :class:`PinnedRun`: either a ``decomposition`` (its combine groups
    and post-combine steps included) with the ``plans`` of every sub-query,
    keyed by sub-query id, or a ``verified`` query (with its bound params) and
    the datasource it belongs to. Exactly one. :meth:`for_sub_query` is the
    one-sub-query case. The question is still asked (the answer is written for
    it), but nothing is routed, decomposed or planned. Each plan passes
    ``plan.after``, the validator and the generator under the caller's scope.
    """

    decomposition: Optional[DecomposerResponse] = None
    plans: Dict[str, PlanModel] = Field(default_factory=dict)
    verified: Optional[VerifiedRef] = None
    verified_datasource_id: Optional[str] = None

    @classmethod
    def for_sub_query(cls, sub_query: SubQuery, plan: PlanModel) -> "SuppliedPlan":
        """A run of one sub-query, standalone, with its plan."""
        decomposition = DecomposerResponse(
            sub_queries=[sub_query],
            combine_groups=[CombineGroup(group_id="supplied", operation="standalone",
                                         inputs=[CombineInput(subquery_id=sub_query.id)])])
        return cls(decomposition=decomposition, plans={sub_query.id: plan})

    @model_validator(mode="after")
    def _one_of(self) -> "SuppliedPlan":
        if (self.decomposition is None) == (self.verified is None):
            raise ValueError("A supplied plan is a decomposition with its plans, or a verified query: exactly one.")
        if self.verified is not None:
            if self.plans:
                raise ValueError("Plans belong to a decomposition; a verified query has none.")
            if not self.verified_datasource_id:
                raise ValueError("A verified query needs verified_datasource_id.")
            return self
        if self.verified_datasource_id is not None:
            raise ValueError("verified_datasource_id goes with a verified query.")
        ids = {sq.id for sq in self.decomposition.sub_queries}
        if set(self.plans) != ids:
            raise ValueError(f"Every sub-query needs its plan and no other plan is allowed; sub-queries: "
                             f"{sorted(ids)}, plans: {sorted(self.plans)}.")
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
    chosen_label: Optional[str] = None  # a chosen plan's option label, for "You chose: ..."

    @property
    def enters_pinned(self) -> bool:
        """Whether the run enters at ``pinned_entry`` (a pinned decomposition); a verified pin does not."""
        return self.decomposition is not None

    @classmethod
    def supplied(cls, plan: SuppliedPlan) -> "PinnedRun":
        """The run ``plan`` pins: its decomposition and plans, or its verified query."""
        if plan.verified is not None:
            return cls(source=PinnedSource.SUPPLIED, verified=plan.verified,
                       verified_datasource_id=plan.verified_datasource_id)
        return cls(source=PinnedSource.SUPPLIED, decomposition=plan.decomposition, plans=dict(plan.plans))


def pinned_from_approvals(approvals: Sequence[Approval], *, question: str, user: Optional[UserContext],
                          hooks: "HookRunner") -> Optional[PinnedRun]:
    """The run the approvals pin, or None when none of them is an approval of this run.

    An approval pins only when its request is this run's (``request_problem``:
    unedited, its SQL its hash, the same question and asker), its hook is one
    of the engine's (``hooks``), and ``by`` holds one of that hook's approver
    roles. Anything else pins nothing: the run plans as usual and the hook
    asks again, so ``plan_source`` is never ``approved`` for a request nobody
    with the role approved. The first valid approval decides: its verified
    query, or its decomposition with the plan of every valid approval made
    from that same decomposition. The SQL is still checked against the hash
    at ``execute.before``.
    """
    valid = []
    for approval in approvals:
        problem = request_problem(approval, question=question, user=user)
        roles = hooks.approver_roles(approval.request.hook)
        if problem is None and not roles:
            problem = f"no hook named '{approval.request.hook}' that may be approved is configured"
        elif problem is None and not set(approval.by.roles) & set(roles):
            problem = "its approver has none of the roles that may approve"
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


def check_pinned(pinned: Optional[PinnedRun], approvals: Sequence[Approval],
                 datasource_id: Optional[str] = None, *, curated: bool = True) -> None:
    """Refuse, at the call and before the run's scope opens, a pin the run cannot take.

    A chosen plan (Wave B's clarification re-ask) is a new question, not an
    approved one, so it cannot come with approvals. A ``datasource_id``
    passed with a pinned plan must be the plan's own datasource. A supplied
    verified query runs only where the curated matcher is in the graph (a
    curated file with a verified query loaded) and never falls back to a
    model run, so without one it is refused here (wave_c.md, D132: a refresh
    uses no tokens). With ``execute`` off the matcher checks it (the caller's
    scope, its values, every table it reads) and runs nothing: a dashboard's
    access check (D135).

    Raises:
        ValueError: a rule is broken.
    """
    if pinned is None:
        return
    if pinned.source == PinnedSource.CHOSEN and approvals:
        raise ValueError("A chosen plan cannot be run with approvals; ask again without them.")
    if pinned.source == PinnedSource.SUPPLIED and pinned.verified is not None:
        if not curated:
            raise ValueError("A supplied verified query needs a curated file with verified queries loaded.")
        if datasource_id is not None and datasource_id != pinned.verified_datasource_id:
            raise ValueError(f"The verified query belongs to the datasource '{pinned.verified_datasource_id}', "
                             f"not datasource_id '{datasource_id}': pass one or the other, or the same one.")
    if datasource_id is not None and pinned.decomposition is not None:
        others = sorted({sq.datasource_id for sq in pinned.decomposition.sub_queries} - {datasource_id})
        if others:
            raise ValueError(f"The plan reads the datasource '{others[0]}', not datasource_id "
                             f"'{datasource_id}': pass one or the other, or the same one.")


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
