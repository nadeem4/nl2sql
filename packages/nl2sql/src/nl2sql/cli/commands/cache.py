"""``nl2sql cache clear``: empty the plan cache kept in the state store (``storage.state``)."""
from __future__ import annotations

import typer

from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

app = typer.Typer(help="Manage the plan cache.", no_args_is_help=True)


@app.command("clear")
def clear(ctx: typer.Context) -> None:
    """Remove every cached plan. Schema snapshots are kept; the next run of each question calls the planner."""
    with build_engine(ctx, check=False) as engine:
        removed = engine.clear_plan_cache()
    console.print(f"[green]Cleared {removed} cached plans.[/green]", soft_wrap=True)
