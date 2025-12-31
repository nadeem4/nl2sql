from rich.console import Console
from nl2sql_cli.commands.utils import run_core_command

console = Console()

def chat_command():
    console.print("[bold green]NL2SQL Interactive Session[/bold green]")
    console.print("Launching Core TUI...\n")

    # Assuming Core has a --chat or similar flag, or tui is default
    # If no flag exists, we might need to add one to Core CLI (Phase 1 update?)
    # or if `tui` is a separate module `nl2sql.tui`.
    # Let's check Core CLI. If it doesn't have --chat, we need to add it.
    # For now, let's assume `run_core_command(["--chat"], stream=True)`
    
    # Wait, TUI in core might be `python -m nl2sql.tui`?
    # If so, we need to adjust `run_core_command` or just direct subprocess call.
    # Let's try invoking the CLI with a hypothetical flag first.
    
    run_core_command(["--chat"], stream=True)

