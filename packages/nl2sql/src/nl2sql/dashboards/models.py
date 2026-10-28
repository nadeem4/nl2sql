"""Dashboards: a titled set of charts in a workspace, each keeping the typed plan that produced it
(docs/architecture/wave_c.md, section 6).

These are the shapes only; ``engine.workspaces`` and ``engine.dashboards`` arrive with D1 to D4. In the
code a chart's slot is a ``Tile``: "chart" and "tile" mean the same thing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import Field, model_validator

from nl2sql.charts.spec import ChartSpec, ColumnKind
from nl2sql.common.strict import StrictModel
from nl2sql.hooks.approval import ApprovalRequest, VerifiedRef
from nl2sql.services.callbacks.token_handler import QuestionUsage

# A user's first pin, or the starter dashboard, creates this workspace when they have none named.
DEFAULT_WORKSPACE_NAME = "General"

GRID_COLUMNS = 4  # the dashboard grid's width

ChartChoice = Literal["auto", "line", "bar", "big_number", "table"]
TileStatus = Literal["ok", "empty", "needs_fixing", "denied", "needs_approval", "stopped", "error"]


class Workspace(StrictModel):
    id: str
    name: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None  # the owner (D118)
    created_at: datetime


class ChartPlan(StrictModel):
    """The typed plan behind a chart: a whole run (its decomposition and a plan per sub-query), or a
    verified query. Exactly one; shaped on ``PinnedRun`` (A1)."""

    decomposition: Optional[Dict[str, Any]] = None  # DecomposerResponse as JSON: sub-queries, combine groups
    plans: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # sub_query_id -> PlanModel as JSON
    verified: Optional[VerifiedRef] = None  # a verified query's id and bound params
    # The datasource the verified query belongs to. A refresh needs it (``SuppliedPlan.verified_datasource_id``);
    # left out, the refresh looks for the one datasource whose curated section holds the query's id.
    verified_datasource_id: Optional[str] = None

    @model_validator(mode="after")
    def _one_of(self) -> "ChartPlan":
        whole = self.decomposition is not None
        if whole == (self.verified is not None):
            raise ValueError("A chart plan is a decomposition with its plans, or a verified query: exactly one.")
        if whole and not self.plans:
            raise ValueError("A decomposition needs the plan of each sub-query.")
        if not whole and self.plans:
            raise ValueError("Plans belong to a decomposition; a verified query has none.")
        if whole and self.verified_datasource_id is not None:
            raise ValueError("verified_datasource_id goes with a verified query.")
        return self


class StoredQuery(StrictModel):
    """One sub-query of an ``ask`` step as stored: what ``add_tile`` and ``replace_plan`` resolve ``from_step``
    to, and what a refresh re-runs."""

    sub_query_id: str
    datasource_id: str
    plan: Optional[Dict[str, Any]] = None  # PlanModel as JSON; None for a verified query
    verified: Optional[VerifiedRef] = None

    @model_validator(mode="after")
    def _one_of(self) -> "StoredQuery":
        if (self.plan is None) == (self.verified is None):
            raise ValueError("A stored query is a plan or a verified query: exactly one.")
        return self


class TileLayout(StrictModel):
    """A place on a 4-column grid."""

    x: int = Field(ge=0, lt=GRID_COLUMNS)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1)

    @model_validator(mode="after")
    def _on_the_grid(self) -> "TileLayout":
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(f"A chart at column {self.x} with width {self.w} leaves the {GRID_COLUMNS}-column grid.")
        return self


class Tile(StrictModel):
    id: str
    title: str  # short: "Revenue by month"
    question: str  # what produced it: the template's, or the ask step's
    plan: ChartPlan
    chart: ChartChoice = "auto"
    layout: TileLayout


class DashboardFilter(StrictModel):
    id: str
    label: str
    column: str  # "datasource.schema.table.column"
    kind: ColumnKind  # its type category, for checking values
    op: Literal["in", "between"]  # values of a dimension, or a range of a temporal column


class ScheduleIdentity(StrictModel):
    """D134: the owner, as they were when they set the schedule. Stored only on the schedule."""

    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)


class Schedule(StrictModel):
    every_minutes: int = Field(ge=1)  # at least dashboards.min_refresh_minutes, checked when it is set
    identity: ScheduleIdentity  # saved when the schedule is set
    set_at: datetime
    next_at: Optional[datetime] = None
    state: Literal["active", "stopped"]
    error: Optional[Dict[str, Any]] = None  # why it stopped: code and message, shown to the owner


class Dashboard(StrictModel):
    id: str
    title: str
    workspace_id: str
    status: Literal["draft", "saved"]  # a draft is built in chat; pin saves it whole
    conversation_id: Optional[str] = None  # the conversation that built the draft
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None  # the owner (D118)
    tiles: List[Tile] = Field(default_factory=list)
    filters: List[DashboardFilter] = Field(default_factory=list)
    schedule: Optional[Schedule] = None
    created_at: datetime
    updated_at: datetime


class TileRun(StrictModel):
    """The last result of a chart; stored with a timestamp."""

    tile_id: str
    at: datetime
    status: TileStatus
    trace_id: Optional[str] = None
    chart: Optional[ChartSpec] = None  # holds the rows, at most charts.max_points
    explanation: Optional[str] = None  # Wave B's explanation of the run, for "why"
    column_kinds: Dict[str, ColumnKind] = Field(default_factory=dict)  # the result's columns; D2 fills it
    filtered: bool = False  # every active filter applied to this chart
    error: Optional[Dict[str, Any]] = None  # the QueryResult's first error, client-safe
    # What a person must approve before this chart runs (status ``needs_approval``): pass an ``Approval`` for
    # each to a manual ``refresh``. Never stored: only a refresh's own result carries them.
    approvals_needed: List[ApprovalRequest] = Field(default_factory=list)


class SkippedTile(StrictModel):
    title: str
    status: str
    reason: str


class DashboardRun(StrictModel):
    dashboard_id: str
    at: datetime
    trigger: Literal["generate", "open", "manual", "schedule"]
    filters: Dict[str, List[Any]] = Field(default_factory=dict)
    tiles: List[TileRun] = Field(default_factory=list)
    skipped: List[SkippedTile] = Field(default_factory=list)  # generate only: template charts not kept
    usage: QuestionUsage = Field(default_factory=QuestionUsage)  # summed over the charts; no model call is made


class DashboardView(StrictModel):
    """What ``open`` returns."""

    dashboard: Dashboard
    runs: List[TileRun] = Field(default_factory=list)  # the stored results, after the access check
    refreshed_at: Optional[datetime] = None  # the oldest ``at`` among the runs
    due: List[str] = Field(default_factory=list)  # tile ids older than the schedule that were not refreshed


class DashboardInfo(StrictModel):
    id: str
    title: str
    workspace_id: str
    status: str
    tiles: int
    refreshed_at: Optional[datetime] = None
    updated_at: datetime


# ---------------------------------------------------------------- edits (D133)
# Every edit is a typed model. The facade's named methods build one each, ``dashboards.edit`` takes a list,
# and the ``dashboard_edit`` chat tool takes the same list, so a person and the model change a dashboard in
# the same ways. No edit calls a model. ``add_tile`` and ``replace_plan`` change what data a chart shows,
# so they exist only in chat.

class StepRef(StrictModel):
    """An ``ask`` step of the conversation, by turn and step number (both 1-based)."""

    turn: int = Field(ge=1)
    step: int = Field(ge=1)


class TileArrangement(TileLayout):
    tile_id: str


class RenameDashboard(StrictModel):
    kind: Literal["rename"] = "rename"
    title: str


class RenameTile(StrictModel):
    kind: Literal["rename_tile"] = "rename_tile"
    tile_id: str
    title: str


class SetTileChart(StrictModel):
    kind: Literal["set_tile_chart"] = "set_tile_chart"
    tile_id: str
    chart: ChartChoice


class RemoveTile(StrictModel):
    kind: Literal["remove_tile"] = "remove_tile"
    tile_id: str


class Arrange(StrictModel):
    kind: Literal["arrange"] = "arrange"
    layouts: List[TileArrangement]


class SetFilters(StrictModel):
    kind: Literal["set_filters"] = "set_filters"
    filters: List[DashboardFilter]


class SetSchedule(StrictModel):
    kind: Literal["set_schedule"] = "set_schedule"
    every_minutes: Optional[int] = Field(default=None, ge=1)  # None removes the schedule


class MoveDashboard(StrictModel):
    kind: Literal["move"] = "move"
    workspace_id: str


class AddTile(StrictModel):
    kind: Literal["add_tile"] = "add_tile"
    title: str
    from_step: StepRef  # the ask step whose stored plans become the chart


class ReplacePlan(StrictModel):
    kind: Literal["replace_plan"] = "replace_plan"
    tile_id: str
    from_step: StepRef  # the fix for a needs_fixing chart


DashboardEdit = Annotated[
    Union[RenameDashboard, RenameTile, SetTileChart, RemoveTile, Arrange, SetFilters, SetSchedule, MoveDashboard,
          AddTile, ReplacePlan],
    Field(discriminator="kind")]

EDIT_KINDS = ("rename", "rename_tile", "set_tile_chart", "remove_tile", "arrange", "set_filters", "set_schedule",
              "move", "add_tile", "replace_plan")
