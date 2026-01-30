from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.columns import Columns
from rich.console import Group
from rich.tree import Tree


class ConsolePresenter:
    def __init__(self):
        self.console = Console()
        self._status = None

    def print_table(self, data: list[dict], title: str = "") -> None:
        table = Table(title=title)
        for key in data[0].keys():
            table.add_column(key)
        for item in data:
            table.add_row(*[str(value) for value in item.values()])
        self.console.print(table)


    def print_success(self, message: str) -> None:
        self.console.print(f"[green][OK][/green] {message}")

    def print_error(self, message: str) -> None:
        self.console.print(f"[red][FAIL][/red] {message}")

    def print_info(self, message: str) -> None:
        self.console.print(f"[blue][INFO][/blue] {message}")

    def print_warning(self, message: str) -> None:
        self.console.print(f"[yellow][WARN][/yellow] {message}")

    def status_context(self, message: str):
        return self.console.status(message)

    def start_interactive_status(self, message: str) -> None:
        self._status = self.console.status(message)
        self._status.start()

    def stop_interactive_status(self) -> None:
        if self._status:
            self._status.stop()
            self._status = None

