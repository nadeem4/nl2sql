import typer
import pathlib
import sys
from typing import Optional
from typing_extensions import Annotated
from rich.console import Console
from rich.table import Table

from nl2sql import PolicyAPI

app = typer.Typer(help="Manage RBAC policies and security.")
console = Console()

from nl2sql_cli.common.decorators import handle_cli_errors

@app.command("validate")
@handle_cli_errors
def validate(
    config: Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config")] = None,
    policies: Annotated[Optional[pathlib.Path], typer.Option("--policies", help="Path to policies.json")] = None,
    secrets: Annotated[Optional[pathlib.Path], typer.Option("--secrets", help="Path to secrets config")] = None,
):
    """
    Validate policy syntax and integrity against defined datasources.
    """
    console.print(f"[bold blue]Validating Policies from:[/bold blue] {policies or 'default'}")

    api = PolicyAPI()
    report = api.validate_policies(policies_path=policies, datasources_path=config, secrets_path=secrets)

    if report.errors:
        console.print(f"[bold red]Schema Validation Failed:[/bold red]\\n{report.errors[0]}")
        sys.exit(1)

    console.print(f"[bold blue]Checking Integrity against Datasources:[/bold blue] {config or 'default'}")
    if report.available_datasources:
        console.print(f"[dim]Available Datasources: {report.available_datasources}[/dim]")

    table = Table(title="Policy Integrity Report")
    table.add_column("Role", style="cyan")
    table.add_column("Target", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Details", style="white")

    for entry in report.entries:
        status = "[green]OK[/green]" if entry.status == "OK" else f"[red]{entry.status}[/red]"
        table.add_row(entry.role, entry.target, status, entry.details)

    console.print(table)

    if not report.ok:
        console.print("\n[bold red]Integrity Check Failed: Policies reference missing resources.[/bold red]")
        sys.exit(1)
    else:
        console.print("\n[bold green]✓ Policy Integrity Verified[/bold green]")
