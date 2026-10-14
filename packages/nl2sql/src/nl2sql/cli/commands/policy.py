"""``nl2sql policy validate``: the config's policies, checked against its datasources."""
import sys

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.common.engine import build_engine

app = typer.Typer(help="Manage RBAC policies and security.")
console = Console()


@app.command("validate")
@handle_cli_errors
def validate(ctx: typer.Context):
    """
    Check that every datasource and table rule the policies name is a configured datasource.
    """
    engine = build_engine(ctx, check=False)
    source = (engine.config.folder / "nl2sql.yaml").as_posix() if engine.config.folder else "Config"
    console.print(f"[bold blue]Validating policies from:[/bold blue] {escape(source)}", soft_wrap=True)
    report = engine.policies.validate()

    if report.errors:
        console.print(f"[bold red]Policy validation failed:[/bold red] {escape(str(report.errors[0]))}")
        sys.exit(1)

    if report.available_datasources:
        console.print(f"[dim]Available datasources: {escape(', '.join(sorted(report.available_datasources)))}[/dim]")

    table = Table(title="Policy Integrity Report")
    table.add_column("Role", style="cyan")
    table.add_column("Target", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Details", style="white")

    for entry in report.entries:
        status = "[green]OK[/green]" if entry.status == "OK" else f"[red]{entry.status}[/red]"
        table.add_row(Text(str(entry.role)), Text(str(entry.target)), status, Text(str(entry.details)))

    console.print(table)

    if not report.ok:
        console.print("\n[bold red]Integrity Check Failed: Policies reference missing resources.[/bold red]")
        sys.exit(1)
    console.print("\n[bold green]✓ Policy Integrity Verified[/bold green]")
