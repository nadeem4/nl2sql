from rich.console import Console
from rich.markup import escape
from rich.table import Table
from nl2sql.datasources.discovery import discover_adapters

from nl2sql.cli.common.decorators import handle_cli_errors

@handle_cli_errors
def list_available_adapters() -> None:
    """Discovers and displays all installed Datasource Adapters."""
    console = Console()
    adapters = discover_adapters()

    if not adapters:
        # `\[` in a normal string is not an escape sequence; Python 3.12 warns
        # about it. `escape()` is how rich is meant to be told the brackets are
        # literal text, not markup.
        console.print(
            "[yellow]No adapters found. Please install a driver extra "
            f"(e.g., {escape('nl2sql[postgres]')}).[/yellow]"
        )
        return

    table = Table(title="Installed Datasource Adapters")
    table.add_column("Adapter ID", style="cyan", no_wrap=True)
    table.add_column("Class", style="magenta")
    table.add_column("Status", style="green")

    for name, cls in adapters.items():
        table.add_row(name, f"{cls.__module__}.{cls.__name__}", "Active")

    console.print(table)
