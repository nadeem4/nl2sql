from nl2sql_cli.commands.utils import run_core_command

def run_command(query: str):
    # Pass through query to Core via CLI args
    # Core CLI needs to support --query argument. 
    # Wait, the current Core CLI (cli.py) only had --json, --diagnose, etc.
    # I need to ensure Core `cli.py` handles a default argument or named argument for query.
    # Looking at `cli.py` (Step 1754), it calls `nl2sql.commands.run.run_pipeline` via `cli.py`.
    # But `cli.py` parsing logic for "query" needs verification.
    
    # Assuming Core CLI accepts `python -m nl2sql.cli "query"` as positional arg.
    # Let's verify Core CLI args first or just assume standard signature.
    # Update: I will check `core/cli.py` again to ensure it accepts a query string.
    
    run_core_command(["--query", query], stream=True)
