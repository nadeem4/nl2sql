"""Stand-ins for ``engine.dashboards`` and ``engine.workspaces`` and the golden models the dashboards routes serve.

``Recorder`` notes every call (name, positional arguments, keyword arguments) and answers from ``returns``: a value,
an exception to raise, or a function of the call. The models are real ones, so a route's response is checked against
the engine's own shapes.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from nl2sql import (ApprovalRequest, Dashboard, DashboardFilter, DashboardView, Tile, TileLayout, TileRun, Workspace)

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class Recorder:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, tuple, Dict[str, Any]]] = []
        self.returns: Dict[str, Any] = {}

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            answer = self.returns.get(name)
            if isinstance(answer, Exception):
                raise answer
            return answer(*args, **kwargs) if callable(answer) else answer

        return call

    def called(self, name: str) -> List[Tuple[tuple, Dict[str, Any]]]:
        return [(args, kwargs) for n, args, kwargs in self.calls if n == name]


def tile(tile_id: str = "t1", *, x: int = 0, title: str = "Revenue by month") -> Tile:
    from nl2sql import ChartPlan, VerifiedRef

    return Tile(id=tile_id, title=title, question="What is revenue by month?",
                plan=ChartPlan(verified=VerifiedRef(id="v_revenue"), verified_datasource_id="chinook"),
                layout=TileLayout(x=x, y=0, w=2, h=2))


def dashboard(status: str = "saved") -> Dashboard:
    return Dashboard(id="d1", title="Sales overview", workspace_id="w1", status=status, user_id="ann",
                     tiles=[tile("t1"), tile("t2", x=2, title="Top countries")],
                     filters=[DashboardFilter(id="f1", label="Country", column="chinook.main.customers.country",
                                              kind="text", op="in")],
                     created_at=AT, updated_at=AT)


def workspace(name: str = "General") -> Workspace:
    return Workspace(id="w1", name=name, user_id="ann", created_at=AT)


def view() -> DashboardView:
    """Four tiles in four states: an answer, one to fix, one the caller may not read, one waiting for approval."""
    from nl2sql import ChartData, ChartSpec, Encoding

    chart = ChartSpec(kind="bar", title="What is revenue by month?", reason="a category and a number",
                      x=Encoding(field="month", kind="text", title="Month"),
                      y=[Encoding(field="revenue", kind="number", title="Revenue")],
                      data=ChartData(trace_id="tr-1", result_id="sq1", columns=["month", "revenue"],
                                     rows=[["2026-01", 120.5], ["2026-02", 98.0]], truncated=False))
    request = ApprovalRequest(id="a" * 64, question="What is revenue by month?", hook="big_reads",
                              datasource_id="chinook", sql="SELECT 1", sql_sha256="b" * 64, reason="Reads a lot.",
                              approver_roles=["lead"])
    base = dashboard()
    tiles = base.tiles + [tile("t3", title="Refunds"), tile("t4", x=2, title="Payroll")]
    return DashboardView(
        dashboard=base.model_copy(update={"tiles": tiles}),
        runs=[TileRun(tile_id="t1", at=AT, status="ok", trace_id="tr-1", chart=chart,
                      column_kinds={"month": "text", "revenue": "number"}),
              TileRun(tile_id="t2", at=AT, status="needs_fixing",
                      error={"code": "PLAN_INVALID", "message": "A column it reads is gone."}),
              TileRun(tile_id="t3", at=AT, status="denied",
                      error={"code": "SECURITY_VIOLATION", "message": "You may not read this."}),
              TileRun(tile_id="t4", at=AT, status="needs_approval", approvals_needed=[request])],
        refreshed_at=AT, due=["t1"])


def run(trigger: str = "manual", **fields: Any):
    from nl2sql import DashboardRun

    return DashboardRun(dashboard_id="d1", at=AT, trigger=trigger, **fields)
