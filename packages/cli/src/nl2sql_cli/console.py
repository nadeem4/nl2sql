import sys

from rich.console import Console
from rich.theme import Theme


def configure_output_encoding() -> None:
    """Make CLI output safe on a console whose code page is not UTF-8.

    Rich renders symbols such as U+2713 that the default Windows code page
    (cp1252) cannot encode, so writing them raised UnicodeEncodeError and
    aborted the command -- `nl2sql setup --demo --lite` died this way.
    Reconfiguring the streams once, at the entry point, is what
    PYTHONIOENCODING=utf-8 did for users who knew to set it; rich reads
    ``sys.stdout`` and its encoding lazily, so consoles built at import time
    pick this up too.

    ``errors="replace"`` is the backstop: if a stream cannot become UTF-8, an
    unencodable symbol degrades to a placeholder instead of killing the
    process. Streams that are not text wrappers -- pytest capture, some
    redirections -- have no ``reconfigure`` and are left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # A detached or already-closed stream. Nothing to configure.
            pass


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
    console.print(f"[success][OK] {message}[/success]")

def print_error(message: str) -> None:
    console.print(f"[error][ERROR] {message}[/error]")
