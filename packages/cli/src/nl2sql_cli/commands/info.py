from rich.console import Console
from rich.table import Table
from nl2sql.datasources.discovery import discover_adapters

def list_available_adapters() -> None:
    """Discovers and displays all installed Datasource Adapters."""
    console = Console()
    adapters = discover_adapters()

    if not adapters:
        console.print("[yellow]No adapters found. Please install an adapter package (e.g., nl2sql-postgres).[/yellow]")
        return

    table = Table(title="Installed Datasource Adapters")
    table.add_column("Adapter ID", style="cyan", no_wrap=True)
    table.add_column("Class", style="magenta")
    table.add_column("Status", style="green")

    for name, cls in adapters.items():
        table.add_row(name, f"{cls.__module__}.{cls.__name__}", "Active")

    console.print(table)
