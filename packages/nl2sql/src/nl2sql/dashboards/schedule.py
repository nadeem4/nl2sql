"""Schedules: when a dashboard is due, and the checks of its saved identity (docs/architecture/wave_c.md, section 6,
D132 and D134).

A schedule re-runs a dashboard as its owner was when they set it: the snapshot of their user, tenant and roles saved on
the schedule. Each run resolves that snapshot against the current policies, so it can only narrow what is allowed now.
A schedule that fails a check stops with a code and a message the owner can read, and nothing retries it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from nl2sql.dashboards.models import Dashboard, Schedule, ScheduleIdentity, Tile, TileRun
from nl2sql.policies import UserContext

ROLE_REMOVED = "SCHEDULE_ROLE_REMOVED"
ACCESS_DENIED = "SCHEDULE_ACCESS_DENIED"
IDENTITY_EXPIRED = "SCHEDULE_IDENTITY_EXPIRED"


def is_due(dashboard: Dashboard, now: datetime) -> bool:
    """Whether the dashboard has an active schedule whose time has come."""
    schedule = dashboard.schedule
    return schedule is not None and schedule.state == "active" and (schedule.next_at is None or schedule.next_at <= now)


def due_tiles(dashboard: Dashboard, last: Dict[str, TileRun], now: datetime) -> List[str]:
    """The charts a schedule would refresh now: no stored result yet, or one older than ``every_minutes``.
    A dashboard with no schedule, or a stopped one, has nothing due."""
    schedule = dashboard.schedule
    if schedule is None or schedule.state != "active":
        return []
    oldest = now - timedelta(minutes=schedule.every_minutes)
    return [t.id for t in dashboard.tiles if t.id not in last or last[t.id].at < oldest]


def owner_as_saved(identity: ScheduleIdentity) -> UserContext:
    return UserContext(user_id=identity.user_id, tenant_id=identity.tenant_id, roles=list(identity.roles))


def identity_problem(schedule: Schedule, policies, now: datetime, max_age_days: int) -> Optional[Tuple[str, str]]:
    """``(code, message)`` when the saved identity may no longer run the schedule: it is older than
    ``dashboards.schedule_identity_max_age_days``, or one of its roles is gone from ``policies``; else None."""
    if now - schedule.set_at > timedelta(days=max_age_days):
        return IDENTITY_EXPIRED, (f"The schedule was set more than {max_age_days} days ago "
                                  f"(dashboards.schedule_identity_max_age_days). Set it again to renew it.")
    gone = [] if policies.allow_all else [role for role in schedule.identity.roles if role not in policies.roles]
    if gone:
        return ROLE_REMOVED, (f"The role{'s' if len(gone) > 1 else ''} {', '.join(gone)} no longer "
                              f"{'exist' if len(gone) > 1 else 'exists'} in the policies. Set the schedule again.")
    return None


def denied_message(denied: List[Tile]) -> str:
    return (f"The owner can no longer read {', '.join(t.title for t in denied)}. "
            "Fix the access or the chart, then set the schedule again.")


def ran(schedule: Schedule, now: datetime) -> Schedule:
    return schedule.model_copy(update={"next_at": now + timedelta(minutes=schedule.every_minutes)})


def stopped(schedule: Schedule, code: str, message: str) -> Schedule:
    return schedule.model_copy(update={"state": "stopped", "next_at": None,
                                       "error": {"code": code, "message": message}})
