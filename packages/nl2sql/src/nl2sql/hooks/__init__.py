"""Hooks: code the operator registers that sees a run at six fixed points.

At each point a hook answers ``Allow``, ``Modify`` (before the validator
only), ``Deny`` or ``NeedsApproval`` (at ``execute.before`` only). Hooks run
in order; the first deny ends the point and modifications chain. A hook that
raises denies, unless it is configured ``on_error: open``. The design is
docs/architecture/wave_a.md, sections 6 and 7; the user guide is
docs/configuration/hooks.md.

The interface lives here, in the engine and not the SDK, because the context
holds engine types (a plan, a sub-query). ``HOOK_POINTS`` (``.points``) says
what each point allows; ``HookRunner`` (``.runner``) runs the hooks the
runtime hands it.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.config.models import HookPoint
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.hooks.approval import Approval
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql_sdk.policy import UserContext


class HookContext(BaseModel):
    """What a hook sees at one point. Frozen; a field the point does not have is None.

    ``tables`` are ``datasource.schema.table``, read by the SQL. ``result`` is
    a summary (columns, row count, truncated), never rows; ``results`` are
    the run's results' summaries at ``answer.after``. ``conversation_id`` is
    set when the run is a conversation's turn (Wave C). ``approvals`` are
    what the run was re-run with (section 7).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    point: HookPoint
    trace_id: str
    question: str
    user: UserContext
    conversation_id: Optional[str] = None
    execute: bool = True
    datasource_id: Optional[str] = None
    datasource_ids: Optional[List[str]] = None
    sub_query: Optional[SubQuery] = None
    plan: Optional[PlanModel] = None
    sql: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    tables: Optional[List[str]] = None
    result: Optional[ResultSummary] = None
    results: Optional[List[ResultSummary]] = None
    final_answer: Optional[Dict[str, Any]] = None
    approvals: List[Approval] = Field(default_factory=list)


class Allow(BaseModel):
    """Let the run go on unchanged."""


class Modify(BaseModel):
    """Go on with these fields changed. Only before the validator, and only the point's own field."""

    question: Optional[str] = None  # run.before
    datasource_ids: Optional[List[str]] = None  # datasources.after: a subset only
    plan: Optional[PlanModel] = None  # plan.after; validated like any other plan


class Deny(BaseModel):
    """Stop: the run, or at a per-sub-query point, only this sub-query (``scope="sub_query"``)."""

    reason: str
    scope: Literal["run", "sub_query"] = "run"


class NeedsApproval(BaseModel):
    """At ``execute.before`` only: a person must approve this SQL before it runs."""

    reason: str


Decision = Union[Allow, Modify, Deny, NeedsApproval]


@runtime_checkable
class Hook(Protocol):
    """A hook plugin: registered under ``nl2sql.hooks``, built from its config block, called at its points."""

    interface_version: ClassVar[int]  # 1
    config_model: ClassVar[type]
    points: ClassVar[frozenset]  # where it may be attached

    @classmethod
    def from_config(cls, cfg: BaseModel) -> "Hook": ...

    def __call__(self, ctx: HookContext) -> Decision: ...


# The table of points and the runner use the shapes above.
from nl2sql.hooks.points import HOOK_POINTS, PointRule  # noqa: E402
from nl2sql.hooks.runner import NO_HOOKS, HookBinding, HookRunner, HookVerdict, build_hook, hooks_of  # noqa: E402

__all__ = ["HOOK_POINTS", "NO_HOOKS", "Allow", "Decision", "Deny", "Hook", "HookBinding", "HookContext",
           "HookPoint", "HookRunner", "HookVerdict", "Modify", "NeedsApproval", "PointRule", "build_hook",
           "hooks_of"]
