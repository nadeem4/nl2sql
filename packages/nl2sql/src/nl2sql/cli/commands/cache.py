"""``nl2sql cache clear``: empty the plan cache kept in the schema store."""
from __future__ import annotations

import pathlib

import typer
from rich.markup import escape

from nl2sql.cli.console import console
from nl2sql.common.settings import settings
from nl2sql.schema import build_schema_store

app = typer.Typer(help="Manage the plan cache.", no_args_is_help=True)


@app.command("clear")
def clear() -> None:
    """Remove every cached plan. Schema snapshots are kept; the next run of each question calls the planner."""
    backend = (settings.schema_store_backend or "sqlite").lower()
    path = pathlib.Path(settings.schema_store_path)
    if backend != "sqlite":
        console.print(f"The '{escape(backend)}' schema store keeps no plan cache between runs; nothing to clear.")
        return
    if not path.exists():
        console.print(f"No plan cache: {escape(str(path.resolve()))} does not exist.")
        return
    store = build_schema_store(backend, settings.schema_store_max_versions, path=path)
    try:
        removed = store.clear_plan_cache()
    finally:
        store.close()
    console.print(f"[green]Cleared {removed} cached plans[/green] from {escape(str(path.resolve()))}.")
