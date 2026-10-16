"""``nl2sql curated``: the curated answers file.

* ``validate`` -- check the ``curated`` section before it ships.
* ``test`` -- run every verified query once against its database.

Verified queries proposed from thumbs-up feedback are in the review queue:
``nl2sql review list --kind verified_query``.
"""
import sys

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.common.engine import build_engine

app = typer.Typer(help="Check and test the curated answers file.", no_args_is_help=True)
console = Console()


@app.command("validate")
@handle_cli_errors
def validate(ctx: typer.Context):
    """
    Validate the curated section: shape, ids, params, read-only SQL in each
    datasource's dialect, and the tables it reads against the indexed schema.
    """
    report = build_engine(ctx, check=False).curated.validate()
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


@app.command("test")
@handle_cli_errors
def test(ctx: typer.Context):
    """
    Run every verified query once against its database, read-only, with each
    param's default or example bound. Exits 1 if any query fails.
    """
    report = build_engine(ctx, check=False).curated.test()
    console.print(f"[bold blue]Testing verified queries from:[/bold blue] {escape(report.path)}")

    for error in report.errors:
        console.print(f"[bold red]Error:[/bold red] {escape(error)}")

    if report.entries:
        table = Table(title="Verified Queries")
        table.add_column("Datasource", style="cyan")
        table.add_column("Query", style="magenta")
        table.add_column("Status")
        table.add_column("Params", style="white")
        table.add_column("Details", style="white")
        for entry in report.entries:
            status = "[green]PASS[/green]" if entry.status == "PASS" else f"[red]{entry.status}[/red]"
            params = ", ".join(f"{k}={v}" for k, v in entry.params.items())
            table.add_row(Text(entry.datasource_id), Text(entry.query_id), status, Text(params), Text(entry.details))
        console.print(table)

    if not report.ok:
        console.print("\n[bold red]Some verified queries failed.[/bold red]")
        sys.exit(1)
    console.print(f"\n[bold green]✓ {len(report.entries)} verified queries ran[/bold green]")
