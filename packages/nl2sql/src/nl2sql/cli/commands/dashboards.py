"""``nl2sql dashboards``: dashboards from the terminal (docs/architecture/wave_c.md, section 6).

* ``refresh`` -- re-run the stored plans of one dashboard (``--dashboard ID``, as ``--as``), or with ``--due`` every dashboard whose
  schedule is due, each as its stored owner. No model call.

A stub: D2 fills it.
"""
from __future__ import annotations

from typing import List, Optional

import typer
from typing_extensions import Annotated

from nl2sql.cli.common.planned import not_yet

app = typer.Typer(help="Dashboards: `refresh`.", no_args_is_help=True)


@app.command("refresh")
def refresh(
    ctx: typer.Context,
    dashboard_id: Annotated[Optional[str], typer.Option("--dashboard", help="The dashboard to refresh")] = None,
    due: Annotated[bool, typer.Option("--due", help="Refresh every dashboard whose schedule is due")] = False,
    as_: Annotated[Optional[List[str]], typer.Option(
        "--as", help="A role to refresh as (repeatable). Without one, policies.default_role.")] = None,
) -> None:
    """Re-run the stored plans of a dashboard, or of every dashboard that is due."""
    not_yet("nl2sql dashboards refresh", "D2")