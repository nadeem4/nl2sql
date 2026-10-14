"""``nl2sql storage purge``: delete the traces and results older than their store's ``max_age_days``."""
from __future__ import annotations

import typer

from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

app = typer.Typer(help="The engine's storage.", no_args_is_help=True)


@app.command("purge")
def purge(ctx: typer.Context) -> None:
    """Apply retention now: traces and results older than their store's max_age_days are deleted."""
    engine = build_engine(ctx, check=False)
    try:
        counts = engine.storage.purge()
    finally:
        engine.close()
    for slot, deleted in counts.items():
        console.print(f"{slot}: deleted {deleted} file(s) past max_age_days")
