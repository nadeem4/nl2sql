"""``nl2sql cache clear``: empty the plan cache kept in the state store (``<storage.path>/state.db``)."""
from __future__ import annotations

import typer
from rich.markup import escape

from nl2sql.cli.common.engine import store_paths
from nl2sql.cli.console import console

app = typer.Typer(help="Manage the plan cache.", no_args_is_help=True)


@app.command("clear")
def clear(ctx: typer.Context) -> None:
    """Remove every cached plan. Schema snapshots are kept; the next run of each question calls the planner."""
    from nl2sql.schema import SqliteSchemaStore

    path = store_paths(ctx).state
    if not path.exists():
        console.print(f"No plan cache: {escape(str(path.resolve()))} does not exist.", soft_wrap=True)
        return
    store = SqliteSchemaStore(path=path)
    try:
        removed = store.clear_plan_cache()
    finally:
        store.close()
    console.print(f"[green]Cleared {removed} cached plans[/green] from {escape(str(path.resolve()))}.", soft_wrap=True)
