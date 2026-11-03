"""Request bodies of the dashboards and workspaces routes. Strict: an unknown
field is a 422. Responses are the engine's own models (``Dashboard``, ``DashboardView``, ``DashboardRun``,
``Workspace``), served as they are."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql import DashboardFilter, TileArrangement


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefreshBody(_Body):
    tile_id: Optional[str] = None  # one chart; else all of them
    filters: Optional[Dict[str, List[Any]]] = None  # filter id -> values; the result is returned, never stored
    approval_ids: List[str] = []  # request ids of granted approvals


class TitleBody(_Body):
    title: str


class ChartBody(_Body):
    chart: Literal["auto", "line", "bar", "big_number", "table"]


class LayoutBody(_Body):
    arrangements: List[TileArrangement]  # a TileLayout and its tile_id each, on the 4-column grid


class FiltersBody(_Body):
    filters: List[DashboardFilter]


class ScheduleBody(_Body):
    every_minutes: Optional[int]  # required: null stops the schedule


class PinBody(_Body):
    workspace_id: Optional[str] = None
    title: Optional[str] = None


class MoveBody(_Body):
    workspace_id: str


class WorkspaceBody(_Body):
    name: str


class ProposalsBody(_Body):
    """The chips of the guided builder: which sources to draw on, and what was ticked."""

    datasource_id: str
    sources: List[Literal["curated", "saved", "asked"]] = Field(min_length=1)
    curated_ids: List[str] = []
    saved_ids: List[str] = []
    metrics: List[str] = []  # glossary terms from the options
    dimensions: List[str] = []  # "datasource.schema.table.column" from the options
    time_range: Optional[str] = None  # a range id from the options
    text: Optional[str] = None
