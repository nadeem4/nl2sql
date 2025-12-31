from rich.console import Console
from rich.theme import Theme

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "command": "bold white on blue",
})

console = Console(theme=custom_theme)

def print_step(message: str) -> None:
    console.print(f"[bold blue]Step:[/bold blue] {message}")

def print_success(message: str) -> None:
    console.print(f"[success]✔ {message}[/success]")

def print_error(message: str) -> None:
    console.print(f"[error]✘ {message}[/error]")
