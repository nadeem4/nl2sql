"""The `dashboards` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", D224).

Thin over ``engine.dashboards``: every handler passes the caller and calls one named method. A dashboard is built as a
draft in chat and pinned whole; these routes read, refresh, edit, pin, move and delete it, and call no model.
Dashboards stay private to their owner: another caller's is a 404. The generate job is ``POST /jobs/dashboard`` in
``routes/jobs.py``.
"""
from typing import Annotated, Any, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Response

from nl2sql import NL2SQL, Dashboard, DashboardInfo, DashboardRun, DashboardView, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.dashboards import (ChartBody, FiltersBody, LayoutBody, MoveBody, PinBody, RefreshBody,
                                          ScheduleBody, TitleBody)

router = APIRouter(tags=["dashboards"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]

_MISSING = {404: {**ERROR_RESPONSE, "description": "No such dashboard (or one that is not the caller's)."},
            **identity.AUTH_RESPONSES}
_EDIT = {**_MISSING, 409: {**ERROR_RESPONSE, "description": "The dashboard is being refreshed or edited."},
         422: {**ERROR_RESPONSE, "description": "The request is refused: its message says why."}}


@router.get("/dashboards", response_model=List[DashboardInfo], summary="List dashboards",
            responses=identity.AUTH_RESPONSES)
def list_dashboards(user: Caller, engine: Engine, workspace_id: Optional[str] = None,
                    status: Literal["saved", "draft"] = "saved"):
    """The caller's dashboards of one `status`, newest first, optionally in one workspace."""
    return engine.dashboards.list(user_context=user, workspace_id=workspace_id, status=status)


@router.get("/dashboards/{dashboard_id}", response_model=DashboardView, summary="Open a dashboard", responses=_MISSING)
def open_dashboard(dashboard_id: str, user: Caller, engine: Engine):
    """The dashboard and the stored results of its charts, after the access check. It only reads: nothing is
    refreshed and no query runs. `due` names the charts older than the schedule; call the refresh route for them."""
    return engine.dashboards.open(dashboard_id, user_context=user, refresh_due=False)


@router.post("/dashboards/{dashboard_id}/refresh", response_model=DashboardRun, summary="Refresh charts",
             responses=_EDIT)
def refresh_dashboard(dashboard_id: str, user: Caller, engine: Engine, body: Optional[RefreshBody] = None):
    """Re-run the stored plans of one chart (`tile_id`) or all of them, as the caller. No model call. `filters`
    maps filter ids to values; a filtered refresh is returned and never stored. `approval_ids` are request ids
    granted through `/approvals`."""
    body = body or RefreshBody()
    return engine.dashboards.refresh(dashboard_id, user_context=user, tile_id=body.tile_id, filters=body.filters,
                                     approvals=body.approval_ids)


@router.get("/dashboards/{dashboard_id}/filters/{filter_id}/values", response_model=List[Any],
            summary="A filter's values", responses=_EDIT)
def filter_values(dashboard_id: str, filter_id: str, user: Caller, engine: Engine, limit: int = 100):
    """The distinct values of an `in` filter's column, or `[min, max]` of a `between` one, as the caller may read
    them (`limit` 1 to 1,000)."""
    return engine.dashboards.filter_values(dashboard_id, filter_id, user_context=user, limit=limit)


@router.put("/dashboards/{dashboard_id}/title", response_model=Dashboard, summary="Rename a dashboard",
            responses=_EDIT)
def rename_dashboard(dashboard_id: str, body: TitleBody, user: Caller, engine: Engine):
    return engine.dashboards.rename(dashboard_id, body.title, user_context=user)


@router.put("/dashboards/{dashboard_id}/tiles/{tile_id}/title", response_model=Dashboard, summary="Rename a chart",
            responses=_EDIT)
def rename_tile(dashboard_id: str, tile_id: str, body: TitleBody, user: Caller, engine: Engine):
    return engine.dashboards.rename_tile(dashboard_id, tile_id, body.title, user_context=user)


@router.put("/dashboards/{dashboard_id}/tiles/{tile_id}/chart", response_model=Dashboard,
            summary="Set how a chart is drawn", responses=_EDIT)
def set_tile_chart(dashboard_id: str, tile_id: str, body: ChartBody, user: Caller, engine: Engine):
    """`auto`, `line`, `bar`, `big_number` or `table`; a kind the chart's last result cannot be drawn as is a 422
    that names the kinds it can."""
    return engine.dashboards.set_tile_chart(dashboard_id, tile_id, body.chart, user_context=user)


@router.delete("/dashboards/{dashboard_id}/tiles/{tile_id}", response_model=Dashboard, summary="Remove a chart",
               responses=_EDIT)
def remove_tile(dashboard_id: str, tile_id: str, user: Caller, engine: Engine):
    """Removes the chart and its stored result: how a chart that needs fixing is taken off."""
    return engine.dashboards.remove_tile(dashboard_id, tile_id, user_context=user)


@router.put("/dashboards/{dashboard_id}/layout", response_model=Dashboard, summary="Arrange the charts",
            responses=_EDIT)
def arrange(dashboard_id: str, body: LayoutBody, user: Caller, engine: Engine):
    """A place on the 4-column grid for each chart named."""
    return engine.dashboards.arrange(dashboard_id, body.arrangements, user_context=user)


@router.put("/dashboards/{dashboard_id}/filters", response_model=Dashboard, summary="Set the filters",
            responses=_EDIT)
def set_filters(dashboard_id: str, body: FiltersBody, user: Caller, engine: Engine):
    """Stores the filter definitions (`in` and `between`); the refresh route applies their values."""
    return engine.dashboards.set_filters(dashboard_id, body.filters, user_context=user)


@router.put("/dashboards/{dashboard_id}/schedule", response_model=Dashboard, summary="Set the refresh schedule",
            responses=_EDIT)
def set_schedule(dashboard_id: str, body: ScheduleBody, user: Caller, engine: Engine):
    """Refresh every `every_minutes`, as the caller; `null` stops it. A period below
    `dashboards.min_refresh_minutes` is a 422."""
    return engine.dashboards.set_schedule(dashboard_id, body.every_minutes, user_context=user)


@router.post("/dashboards/{dashboard_id}/pin", response_model=Dashboard, summary="Pin a draft", responses=_EDIT)
def pin_dashboard(dashboard_id: str, user: Caller, engine: Engine, body: Optional[PinBody] = None):
    """Saves a draft whole into a workspace (else the one it is in, else "General"). A saved dashboard or an empty
    draft is a 422."""
    body = body or PinBody()
    return engine.dashboards.pin(dashboard_id, user_context=user, workspace_id=body.workspace_id, title=body.title)


@router.post("/dashboards/{dashboard_id}/move", response_model=Dashboard, summary="Move to another workspace",
             responses=_EDIT)
def move_dashboard(dashboard_id: str, body: MoveBody, user: Caller, engine: Engine):
    return engine.dashboards.move(dashboard_id, body.workspace_id, user_context=user)


@router.delete("/dashboards/{dashboard_id}", status_code=204, summary="Delete a dashboard", responses=_EDIT)
def delete_dashboard(dashboard_id: str, user: Caller, engine: Engine):
    """Deletes the dashboard, its stored results and its schedule."""
    engine.dashboards.delete(dashboard_id, user_context=user)
    return Response(status_code=204)


@router.get("/datasources/{datasource_id}/starter-questions", response_model=List[str],
            summary="Starter questions",
            responses={422: {**ERROR_RESPONSE, "description": "The datasource has no index, or the caller "
                                                               "cannot reach it."}, **identity.AUTH_RESPONSES})
def starter_questions(datasource_id: str, user: Caller, engine: Engine, n: Annotated[int, Query(ge=1)] = 5):
    """The questions of the first `n` charts the starter dashboard would build, as the caller may read the
    datasource. Nothing runs and nothing is saved. Empty when the caller reaches no table."""
    return engine.dashboards.suggest_questions(datasource_id, user_context=user, n=n)
