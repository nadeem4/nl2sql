import typer
import pathlib
import sys
from typing import Optional
from typing_extensions import Annotated
from rich.console import Console
from rich.table import Table

from nl2sql.common.settings import settings
from nl2sql.configs import PolicyFileConfig
from nl2sql.datasources import DatasourceRegistry
from nl2sql.secrets import secret_manager

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
    # Resolve paths (Global callback may have updated settings)
    if config is None:
        config = pathlib.Path(settings.datasource_config_path)
    if policies is None:
        policies = pathlib.Path(settings.policies_config_path)
    if secrets is None:
        secrets = pathlib.Path(settings.secrets_config_path)

    from nl2sql.configs import ConfigManager
    cm = ConfigManager()
    
    console.print(f"[bold blue]Validating Policies from:[/bold blue] {policies}")
    
    # 1. Load Policies (Schema Check)
    try:
        policy_cfg = cm.load_policies(policies)
        console.print("[green]✓ Schema Syntax Valid[/green]")
    except Exception as e:
        console.print(f"[bold red]Schema Validation Failed:[/bold red]\\n{e}")
        sys.exit(1)

    # 2. Load Datasources (Integrity Check)
    console.print(f"[bold blue]Checking Integrity against Datasources:[/bold blue] {config}")
    try:
        # Load and Configure Secrets
        secret_configs = cm.load_secrets(secrets)
        if secret_configs:
            secret_manager.configure(secret_configs)
        
        # Load Datasources
        ds_configs = cm.load_datasources(config)
        registry = DatasourceRegistry(ds_configs)
        
        available_ds = set(registry.list_ids())
        console.print(f"[dim]Available Datasources: {available_ds}[/dim]")
        
        has_errors = False
        
        table = Table(title="Policy Integrity Report")
        table.add_column("Role", style="cyan")
        table.add_column("Target", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Details", style="white")

        for role_id, role_def in policy_cfg.roles.items():
            # Check Datasources
            for ds in role_def.allowed_datasources:
                if ds == "*":
                    table.add_row(role_id, "Datasource: *", "[green]OK[/green]", "Global Access")
                    continue
                    
                if ds not in available_ds:
                    table.add_row(role_id, f"Datasource: {ds}", "[red]MISSING[/red]", "Datasource not defined in config")
                    has_errors = True
                else:
                    table.add_row(role_id, f"Datasource: {ds}", "[green]OK[/green]", "Verified")

            # Check Tables
            for rule in role_def.allowed_tables:
                if rule == "*":
                     table.add_row(role_id, "Table: *", "[green]OK[/green]", "Global Access")
                     continue
                
                parts = rule.split(".")
                if len(parts) >= 2:
                    ds_part = parts[0]
                    if ds_part not in available_ds and ds_part != "*":
                         table.add_row(role_id, f"Table Rule: {rule}", "[red]INVALID DS[/red]", f"Datasource '{ds_part}' unknown")
                         has_errors = True
                    else:
                        table.add_row(role_id, f"Table Rule: {rule}", "[green]OK[/green]", "DS Verified")
        
        console.print(table)
        
        if has_errors:
            console.print("\n[bold red]Integrity Check Failed: Policies reference missing resources.[/bold red]")
            sys.exit(1)
        else:
            console.print("\n[bold green]✓ Policy Integrity Verified[/bold green]")

    except Exception as e:
        console.print(f"[bold red]Integrity Check Failed:[/bold red] {e}")
        sys.exit(1)
