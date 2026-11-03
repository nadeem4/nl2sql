"""A saved answer and the report of saving one."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from nl2sql.common.strict import StrictModel
from nl2sql.dashboards.models import ChartChoice, ChartPlan


class SavedAnswer(StrictModel):
    """One answer its owner kept: the question and the typed plan that answered it. No rows and no SQL text are
    stored; a re-run builds both again under the viewer's scope."""

    id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None  # the owner; nobody else reads it, an admin included
    question: str
    datasource_id: str
    plan: ChartPlan
    chart: ChartChoice = "auto"
    created_at: datetime  # UTC
    trace_id: str  # the run it came from


class SaveReport(StrictModel):
    """What ``engine.answers.save`` did. The answer is always saved; the proposal may not be filed."""

    answer: SavedAnswer
    proposed: bool  # a ``verified_query`` proposal is in the review queue for this run
    proposal_id: Optional[str] = None
    not_proposed_reason: Optional[str] = None  # why not: ``skip_reason``'s word (``verified``, ``multiple_statements``, ...), or ``no_trace``
