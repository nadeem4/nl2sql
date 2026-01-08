import typer
import pathlib
import sys
from typing_extensions import Annotated
from rich.console import Console
from rich.table import Table

from nl2sql.common.settings import settings
from nl2sql.security.policies import PolicyConfig
from nl2sql.datasources.config import load_configs
from nl2sql.datasources import DatasourceRegistry
from pydantic import ValidationError
from nl2sql.secrets import secret_manager, load_secret_configs

app = typer.Typer(help="Manage RBAC policies and security.")
console = Console()

@app.command("validate")
def validate(
    config: Annotated[pathlib.Path, typer.Option("--config", help="Path to datasource config")] = pathlib.Path(settings.datasource_config_path),
    policies: Annotated[pathlib.Path, typer.Option("--policies", help="Path to policies.json")] = pathlib.Path(settings.policies_config_path),
    secrets: Annotated[pathlib.Path, typer.Option("--secrets", help="Path to secrets config")] = pathlib.Path(settings.secrets_config_path),
):
    """
    Validate policy syntax and integrity against defined datasources.
    """
    console.print(f"[bold blue]Validating Policies from:[/bold blue] {policies}")
    
    # 1. Load Policies (Schema Check)
    try:
        if not policies.exists():
            console.print(f"[bold red]Error:[/bold red] Policy file not found at {policies}")
            sys.exit(1)
            
        with open(policies, "r") as f:
            raw_json = f.read()
            
        policy_cfg = PolicyConfig.model_validate_json(raw_json)
        console.print("[green]✓ Schema Syntax Valid[/green]")
        
    except ValidationError as ve:
        console.print(f"[bold red]Schema Validation Failed:[/bold red]\n{ve}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Error loading policies:[/bold red] {e}")
        sys.exit(1)

    # 2. Load Datasources (Integrity Check)
    console.print(f"[bold blue]Checking Integrity against Datasources:[/bold blue] {config}")
    try:
        # Secrets are needed to load datasources properly
        if secrets.exists():
            secret_configs = load_secret_configs(secrets)
            secret_manager.configure(secret_configs)
        
        ds_configs = load_configs(config)
        registry = DatasourceRegistry(ds_configs)
        
        available_ds = set(registry.list_ids())
        console.print(f"[dim]Available Datasources: {available_ds}[/dim]")
        
        has_errors = False
        
        table = Table(title="Policy Integrity Report")
        table.add_column("Role", style="cyan")
        table.add_column("Target", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Details", style="white")

        for role_id, role_def in policy_cfg.root.items():
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

            # Check Tables (Heuristic only - we can't verify tables without checking DB connection, which is slow/expensive. 
            # But we CAN verify the component 'datasource' part of 'datasource.table')
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
