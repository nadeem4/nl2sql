import sys
import importlib.util
from rich.table import Table
from rich.panel import Panel
from nl2sql_cli.console import console, print_success, print_error
from nl2sql_cli.config import KNOWN_ADAPTERS
from nl2sql_cli.checks import check_package, verify_connectivity

from nl2sql_cli.common.decorators import handle_cli_errors

@handle_cli_errors
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
    
    console.print(table)
    
    # 4. Connectivity Check
    console.print("\n[bold]Connectivity:[/bold]")
    verify_connectivity(print_table=True)

