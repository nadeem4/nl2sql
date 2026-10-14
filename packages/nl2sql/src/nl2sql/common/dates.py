"""The run date the prompts are told."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional


def run_date(today: Optional[date]) -> date:
    """``today`` when the date is pinned (``settings.pipeline.today``), else the UTC date."""
    return today or datetime.now(timezone.utc).date()
