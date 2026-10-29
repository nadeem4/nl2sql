"""The ``dashboard_pin`` chat tool (docs/architecture/wave_c.md, sections 4 and 6).

Saves the dashboard being built, whole, through ``engine.dashboards.pin``. A chart is never pinned alone, and the
model is told to call this only when the user asks. No model call. Like every chat tool it imports only the
top-level ``nl2sql`` and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class DashboardPinArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_id: Optional[str] = Field(default=None, description="The dashboard to save; this conversation's when absent.")
    workspace_id: Optional[str] = Field(default=None, description="Where to save it; the workspace it is in, else General.")
    title: Optional[str] = Field(default=None, description="A new title for it, when the user gave one.")


class DashboardPinTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Save the dashboard being built, whole, into a workspace. Call it only when the user asks."
    args_model: ClassVar[type[BaseModel]] = DashboardPinArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        assert isinstance(args, DashboardPinArgs)
        dashboard_id = args.dashboard_id or ctx.conversation.dashboard_id
        if dashboard_id is None:
            raise ToolNotApplicable("There is no dashboard in this conversation to save: add a chart to one first.")
        try:
            dashboard = ctx.engine.dashboards.pin(dashboard_id, user_context=ctx.user, workspace_id=args.workspace_id,
                                                  title=args.title)
        except (ValueError, LookupError) as error:  # empty or already saved, not the caller's, a bad workspace
            raise ToolNotApplicable(str(error)) from None
        body = {"id": dashboard.id, "title": dashboard.title, "status": dashboard.status,
                "workspace_id": dashboard.workspace_id, "tiles": len(dashboard.tiles)}
        return ToolOutput(kind="json", title=dashboard.title, body=body, refs={"dashboard_id": dashboard.id})