"""``nl2sql curated validate``: check ``curated.yaml`` before it ships."""
import pathlib
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typing_extensions import Annotated

from nl2sql import CuratedAPI
from nl2sql.cli.common.decorators import handle_cli_errors

app = typer.Typer(help="Check the curated answers file.", no_args_is_help=True)
console = Console()


@app.command("validate")
@handle_cli_errors
def validate(
    curated: Annotated[Optional[pathlib.Path], typer.Option("--curated", help="Path to curated.yaml")] = None,
    config: Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config")] = None,
    secrets: Annotated[Optional[pathlib.Path], typer.Option("--secrets", help="Path to secrets config")] = None,
):
    """
    Validate curated.yaml: shape, ids, params, read-only SQL in each datasource's
    dialect, and the tables it reads against the indexed schema.
    """
    report = CuratedAPI().validate(curated_path=curated, datasources_path=config, secrets_path=secrets)
    console.print(f"[bold blue]Validating curated answers from:[/bold blue] {escape(report.path)}")

    for error in report.errors:
        console.print(f"[bold red]Error:[/bold red] {escape(error)}")

    if report.entries:
        table = Table(title="Verified Queries")
        table.add_column("Datasource", style="cyan")
        table.add_column("Query", style="magenta")
        table.add_column("Status")
        table.add_column("Details", style="white")
        for entry in report.entries:
            status = "[green]OK[/green]" if entry.status == "OK" else f"[red]{entry.status}[/red]"
            table.add_row(Text(entry.datasource_id), Text(entry.query_id), status, Text(entry.details))
        console.print(table)

    for note in report.notes:
        console.print(f"[yellow]Note:[/yellow] {escape(note)}")

    if not report.ok:
        console.print("\n[bold red]Curated answers file has errors.[/bold red]")
        sys.exit(1)
    console.print("\n[bold green]✓ Curated answers file is valid[/bold green]")
