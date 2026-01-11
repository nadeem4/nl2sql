from functools import wraps
import sys
import traceback
from rich.console import Console
from nl2sql.common.exceptions import NL2SQLError

console = Console()

def handle_cli_errors(func):
    """
    Decorator to wrap CLI commands with unified error handling.
    
    - NL2SQLError: Prints a clean red error message.
    - KeyboardInterrupt: Exits gracefully.
    - Unexpected Exception: Prints stack trace and error.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except NL2SQLError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Operation cancelled by user.[/yellow]")
            sys.exit(130)  # Standard SIGINT exit code
        except Exception as e:
            console.print(f"[bold red]Unexpected Error:[/bold red] {e}")
            console.print(traceback.format_exc())
            console.print("[dim]Please report this bug to the nl2sql team.[/dim]")
            sys.exit(1)
            
    return wrapper
