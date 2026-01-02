import typer
from nl2sql_cli.console import console

app = typer.Typer(
    name="nl2sql",
    help="Unified CLI for the NL2SQL Ecosystem",
    no_args_is_help=False, # Changed to False so we can handle default interaction
    add_completion=False,
)

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """
    Unified CLI for the NL2SQL Ecosystem.
    """
    if ctx.invoked_subcommand is None:
        # Default behavior: Launch Chat
        from nl2sql_cli.commands.chat import chat_command
        chat_command()

@app.command()
def doctor():
    """Diagnose environment issues."""
    from nl2sql_cli.commands.doctor import doctor_command
    doctor_command()

@app.command()
def setup():
    """Interactive setup wizard."""
    from nl2sql_cli.commands.setup import setup_command
    setup_command()

@app.command()
def install(package: str):
    """Install an adapter package."""
    from nl2sql_cli.commands.install import install_command
    install_command(package)

@app.command()
def chat():
    """Launch interactive chat (TUI)."""
    from nl2sql_cli.commands.chat import chat_command
    chat_command()

@app.command()
def run(query: str):
    """Run a single query."""
    from nl2sql_cli.commands.run import run_command
    run_command(query)

if __name__ == "__main__":
    app()
