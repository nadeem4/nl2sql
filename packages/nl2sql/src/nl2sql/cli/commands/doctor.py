import sys
import importlib.util
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from nl2sql.cli.console import console, print_success, print_error
from nl2sql.cli.config import ADAPTER_DRIVERS, KNOWN_ADAPTERS
from nl2sql.cli.checks import check_package, verify_connectivity

from nl2sql.cli.common.decorators import handle_cli_errors

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
    if importlib.util.find_spec("nl2sql"):
        print_success("Core package (nl2sql) installed.")
    else:
        print_error("Core package (nl2sql) NOT found.")

    # 3. Adapters
    console.print("\n[bold]Adapters:[/bold]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Database")
    table.add_column("Package")
    table.add_column("Status")

    for name, pkg in KNOWN_ADAPTERS.items():
        # The adapter module always ships with nl2sql; the driver is what an
        # extra adds, so that is what decides whether the dialect is usable.
        ok = check_package(ADAPTER_DRIVERS[name])
        status = "[green]Installed[/green]" if ok else "[red]Missing[/red]"
        table.add_row(name, escape(pkg), status)
    
    console.print(table)
    
    # 4. Connectivity Check
    console.print("\n[bold]Connectivity:[/bold]")
    verify_connectivity(print_table=True)

