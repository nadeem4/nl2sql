import os
import sys
import importlib.util
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from nl2sql.common.env_hint import active_env_file
from nl2sql.configs.manager import ConfigManager
from nl2sql.llm.registry import PROVIDER_PRESETS
from nl2sql.cli.console import console, print_success, print_error
from nl2sql.cli.config import ADAPTER_DRIVERS, KNOWN_ADAPTERS
from nl2sql.cli.checks import check_package, verify_connectivity

from nl2sql.cli.common.decorators import handle_cli_errors

@handle_cli_errors
def doctor_command():
    console.print(Panel("[bold cyan]NL2SQL Doctor[/bold cyan]"))

    # 1. Python Version
    py_ver = sys.version.split()[0]
    console.print(f"Python Version: {py_ver}")
    if sys.version_info < (3, 12):
        print_error("Python 3.12+ required.")
    else:
        print_success("Python version OK.")

    # 2. Core Check
    if importlib.util.find_spec("nl2sql"):
        print_success("Core package (nl2sql) installed.")
    else:
        print_error("Core package (nl2sql) NOT found.")

    # 3. Adapters
    console.print("\n[bold]Adapters:[/bold]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Database")
    table.add_column("Package")
    table.add_column("Status")

    for name, pkg in KNOWN_ADAPTERS.items():
        # The adapter module always ships with nl2sql; the driver is what an
        # extra adds, so that is what decides whether the dialect is usable.
        ok = check_package(ADAPTER_DRIVERS[name])
        status = "[green]Installed[/green]" if ok else "[red]Missing[/red]"
        table.add_row(name, escape(pkg), status)
    
    console.print(table)
    
    # 4. Connectivity Check
    console.print("\n[bold]Connectivity:[/bold]")
    verify_connectivity(print_table=True)

    # 5. LLM credential check
    #
    # Everything above this point can be green while a query still cannot run:
    # the missing key is the single most common first-run failure, and doctor
    # is where a stuck user looks. Reporting it is diagnosis, so a broken LLM
    # config is printed, not raised.
    console.print("\n[bold]LLM:[/bold]")
    try:
        agent = ConfigManager().load_llm().default
        preset = PROVIDER_PRESETS.get(agent.provider)
        env_var = preset.api_key_env if preset else None
        configured_key = agent.api_key.get_secret_value() if agent.api_key else ""
        has_key = (
            bool(preset is not None and preset.api_key_placeholder)
            or bool(env_var and os.environ.get(env_var))
            # A "${env:VAR}" reference is a pointer, not a key; only a literal
            # value in the config counts on its own.
            or bool(configured_key and not configured_key.startswith("${env:"))
        )
        if has_key:
            print_success(f"LLM {agent.provider}/{agent.model}: key found.")
        else:
            print_error(
                f"MISSING: LLM {agent.provider}/{agent.model} needs {env_var}. "
                f"Set it in {active_env_file()} or the environment."
            )
    except Exception as exc:
        print_error(f"LLM configuration could not be loaded: {exc}")

