from rich.panel import Panel
import sys
import importlib.util
from rich.table import Table
from rich.panel import Panel
from nl2sql_cli.console import console, print_success, print_error
from nl2sql_cli.config import KNOWN_ADAPTERS, CORE_PACKAGE

def check_package(name: str) -> bool:
    import_name = name.replace("-", "_")
    return importlib.util.find_spec(import_name) is not None

def doctor_command():
    console.print(Panel("[bold cyan]NL2SQL Doctor[/bold cyan]"))

    # 1. Python Version
    py_ver = sys.version.split()[0]
    console.print(f"Python Version: {py_ver}")
    if sys.version_info < (3, 9):
        print_error("Python 3.9+ required.")
    else:
        print_success("Python version OK.")

    # 2. Core Check
    if check_package("nl2sql-core"): 
        if importlib.util.find_spec("nl2sql"):
            print_success("Core package (nl2sql-core) installed.")
        else:
            print_error("Core package (nl2sql-core) NOT found.")
    else:
        if importlib.util.find_spec("nl2sql"):
            print_success("Core package (nl2sql) installed.")
        else:
            print_error("Core package (nl2sql-core) NOT found.")

    # 3. Adapters
    console.print("\n[bold]Adapters:[/bold]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Database")
    table.add_column("Package")
    table.add_column("Status")

    for name, pkg in KNOWN_ADAPTERS.items():
        import_name = pkg.replace("-", "_")
        status = "[green]Installed[/green]" if check_package(import_name) else "[red]Missing[/red]"
        table.add_row(name, pkg, status)
    
    
    # 4. Connectivity Check
    console.print("\n[bold]Connectivity:[/bold]")
    
    from nl2sql_cli.commands.utils import run_core_command
    
    data = run_core_command(["--diagnose", "--json"], capture_json=True)
    
    if data and "connectivity" in data:
        results = data["connectivity"]
        conn_table = Table(show_header=True, header_style="bold cyan")
        conn_table.add_column("Datasource ID")
        conn_table.add_column("Status")
        conn_table.add_column("Details")
        
        for ds_id, info in results.items():
            success = info.get("ok", False)
            msg = info.get("details", "")
            
            status = "[green]OK[/green]" if success else "[red]Failed[/red]"
            details = msg if not success else ""
            conn_table.add_row(ds_id, status, details)
        
        console.print(conn_table)
    else:
        # If data is None, run_core_command printed the error already
        console.print("[yellow]Connectivity check incomplete.[/yellow]")

