"""``nl2sql dashboards``: dashboards from the terminal (docs/architecture/wave_c.md, section 6).

* ``refresh`` -- re-run the stored plans of one dashboard (``--dashboard ID``, as ``--as`` builds the caller), or with
  ``--due`` every dashboard whose schedule is due, each as its stored owner (``run_scheduled``, so cron can drive
  schedules). No model call.

Each is one call on ``engine.dashboards``; a refusal exits 1.
"""
from __future__ import annotations

from collections import Counter
from typing import List, Optional

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.commands.knowledge import AsOption, caller
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

app = typer.Typer(help="Dashboards: `refresh`.", no_args_is_help=True)


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}", soft_wrap=True)
    raise typer.Exit(1)


def _print(run) -> None:
    counts = ", ".join(f"{status} {n}" for status, n in sorted(Counter(t.status for t in run.tiles).items()))
    console.print(f"refreshed {escape(run.dashboard_id)} ({run.trigger}): {escape(counts or 'no charts')}",
                  soft_wrap=True)
    for tile in run.tiles:
        if tile.status not in ("ok", "empty"):
            why = f" - {escape(tile.error['message'])}" if tile.error else ""
            console.print(f"  {escape(tile.tile_id)}: {tile.status}{why}", soft_wrap=True)


@app.command("refresh")
def refresh(
    ctx: typer.Context,
    dashboard_id: Annotated[Optional[str], typer.Option("--dashboard", help="The dashboard to refresh")] = None,
    due: Annotated[bool, typer.Option("--due", help="Refresh every dashboard whose schedule is due")] = False,
    as_: AsOption = None,
    user: Annotated[Optional[str], typer.Option(
        "--user", help="The owner to act as (with --dashboard). Operator use: whoever holds the config and the "
                       "credentials may act as any owner.")] = None,
    tenant: Annotated[Optional[str], typer.Option(
        "--tenant", help="The owner's tenant (with --dashboard). Operator use, as --user.")] = None,
) -> None:
    """Re-run the stored plans of a dashboard, or of every dashboard that is due.

    --dashboard runs as the caller --as, --user and --tenant build: the operator, who holds the config and the
    credentials, acts as that owner. --due runs each schedule as its saved owner and takes none of them.
    """
    if due == (dashboard_id is not None):
        _fail("Pass --dashboard ID to refresh one dashboard, or --due to run the schedules that are due.")
    if due and (as_ or user is not None or tenant is not None):
        _fail("--due runs each schedule as its owner: pass no --as, --user or --tenant with it.")
    engine = build_engine(ctx)
    with engine:
        if due:
            runs = engine.dashboards.run_scheduled()
            if not runs:
                console.print("No dashboard is due.", soft_wrap=True)
            for run in runs:
                _print(run)
            return
        try:
            who = caller(engine, list(as_ or []), user).model_copy(update={"tenant_id": tenant})
            _print(engine.dashboards.refresh(dashboard_id, user_context=who))
        except (LookupError, ValueError) as error:
            _fail(str(error))
