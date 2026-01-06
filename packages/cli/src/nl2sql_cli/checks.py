import importlib.util
from typing import Dict, Tuple
import pathlib
from rich.table import Table
from nl2sql_cli.console import console, print_success, print_error

def check_package(name: str) -> bool:
    """Checks if a python package is installed."""
    import_name = name.replace("-", "_")
    return importlib.util.find_spec(import_name) is not None

def verify_connectivity(print_table: bool = True) -> bool:
    """
    Checks connectivity for all profiles in the default config.
    Returns True if all checks passed, False otherwise.
    """
    from nl2sql.common.settings import settings
    from nl2sql.datasources import load_profiles
    from nl2sql.diagnostics import check_connectivity as core_check
    
    try:
        profiles = load_profiles(pathlib.Path(settings.datasource_config_path))
        
        with console.status("[bold green]Verifying connectivity...[/bold green]"):
            results = core_check(list(profiles.values()))
        
        all_ok = True
        
        if print_table:
            conn_table = Table(show_header=True, header_style="bold cyan")
            conn_table.add_column("Datasource ID")
            conn_table.add_column("Status")
            conn_table.add_column("Details")
        
        for ds_id, (success, msg) in results.items():
            if not success:
                all_ok = False
            
            if print_table:
                status = "[green]OK[/green]" if success else "[red]Failed[/red]"
                details = msg if not success else ""
                conn_table.add_row(ds_id, status, details)
        
        if print_table:
            console.print(conn_table)
            
        return all_ok

    except Exception as e:
         console.print(f"[red]Connectivity check failed: {e}[/red]")
         return False
