from rich.console import Console
from rich.markup import escape
from rich.table import Table
from nl2sql.registry import Registry, RegistryError

from nl2sql.cli.common.decorators import handle_cli_errors

@handle_cli_errors
def list_available_adapters() -> None:
    """Lists the adapters the registry knows, and whether each one can be used."""
    console = Console()
    registry = Registry.default()
    names = registry.names("adapter")

    if not names:
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

    for name in names:
        try:
            cls = registry.load("adapter", name)
        except RegistryError as e:
            table.add_row(name, "", f"[red]{escape(str(e))}[/red]")
        else:
            table.add_row(name, f"{cls.__module__}.{cls.__name__}", "Ready")

    console.print(table)
