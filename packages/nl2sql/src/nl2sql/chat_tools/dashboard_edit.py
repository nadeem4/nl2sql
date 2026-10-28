"""The ``dashboard_edit`` chat tool (docs/architecture/wave_c.md, sections 4 and 6).

Builds or changes a dashboard through ``engine.dashboards.edit``: the same typed edits a person makes by hand, plus
``add_tile`` and ``replace_plan``, which turn an ``ask`` step of the conversation into a chart. The plan comes from
the engine's own stored step, never from model text, and is checked by a run that executes nothing before it is
saved. The first use creates a draft tied to the conversation. No model call, no rows. Like every chat tool it
imports only the top-level ``nl2sql`` and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql import DashboardEdit
from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class DashboardEditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edits: List[DashboardEdit] = Field(min_length=1, description=(
        "The changes, applied in order, all or none. add_tile turns an answer you asked for into a chart: from_step "
        "is its turn number and the position of the ask call among that turn's ask calls, counting from 1. "
        "replace_plan fixes a chart marked needs_fixing from a new answer to its question. You never give a plan or "
        "SQL: the chart keeps the plan the engine made for that answer."))
    dashboard_id: Optional[str] = Field(default=None, description=(
        "The dashboard to change; this conversation's dashboard when absent, made as a draft on first use."))
    title: Optional[str] = Field(default=None, description="The title of a new draft.")
    workspace_id: Optional[str] = Field(default=None, description="The workspace of a new draft; General when absent.")


class DashboardEditTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = (
        "Build or change a dashboard: add a chart from an answer, rename, rearrange or remove charts, fix a broken chart.")
    args_model: ClassVar[type[BaseModel]] = DashboardEditArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        assert isinstance(args, DashboardEditArgs)
        dashboards, user = ctx.engine.dashboards, ctx.user
        dashboard_id = args.dashboard_id or ctx.conversation.dashboard_id
        made = False
        try:
            if dashboard_id is None:
                dashboard_id = dashboards.create_draft(user_context=user, title=args.title,
                                                       workspace_id=args.workspace_id,
                                                       conversation_id=ctx.conversation.id).id
                made = True
            dashboard = dashboards.edit(dashboard_id, args.edits, user_context=user, conversation=ctx.conversation)
        except (ValueError, LookupError) as error:  # a refused edit, a plan the check rejected, not the caller's
            if made:
                dashboards.delete(dashboard_id, user_context=user)  # no empty draft left behind
            raise ToolNotApplicable(str(error)) from None
        body = {"id": dashboard.id, "title": dashboard.title, "status": dashboard.status,
                "workspace_id": dashboard.workspace_id,
                "tiles": [{"id": t.id, "title": t.title} for t in dashboard.tiles]}
        return ToolOutput(kind="json", title=dashboard.title, body=body, refs={"dashboard_id": dashboard.id})