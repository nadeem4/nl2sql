"""What a command stub does until the row that fills it lands (docs/architecture/wave_c.md, row C0)."""
from __future__ import annotations

import typer
from rich.markup import escape

from nl2sql.cli.console import console


def not_yet(command: str, row: str) -> None:
    """Say which row of Wave C's plan fills ``command`` and exit 1."""
    console.print(f"[bold red]Error:[/bold red] {escape(command)} arrives with {row} "
                  f"(docs/architecture/wave_c.md, row {row}).", soft_wrap=True)
    raise typer.Exit(1)