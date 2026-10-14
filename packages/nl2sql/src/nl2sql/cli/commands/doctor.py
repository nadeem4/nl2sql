"""``nl2sql doctor``: what is installed, which provider keys are set, and whether the configured engine is ready.

Doctor is what a stuck user runs, so nothing here may escape as an exception:
a config that does not load, a datasource that does not connect and a missing
key are reported, not raised. A config's own problems are ``load_config``'s
to name; doctor prints them as they come.
"""
import sys
import importlib.util

import typer
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from nl2sql.cli.common.engine import config_option
from nl2sql.llm.providers import KEYED_PROVIDERS, api_key_from_env, env_var_for_provider
from nl2sql.llm.wires.anthropic import EXTRA_HINT as ANTHROPIC_EXTRA_HINT
from nl2sql.cli.console import console, print_success, print_error
from nl2sql.cli.config import ADAPTER_DRIVERS, KNOWN_ADAPTERS
from nl2sql.cli.checks import check_package

from nl2sql.cli.common.decorators import handle_cli_errors


@handle_cli_errors
def doctor_command(ctx: typer.Context):
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

    # 4. Provider keys: which standard variables are set. The missing key is
    # the most common first-run failure; the config may name another
    # variable, which the engine check below reports.
    console.print("\n[bold]Provider keys:[/bold]")
    for provider in KEYED_PROVIDERS:
        variable = env_var_for_provider(provider)
        if api_key_from_env(provider):
            print_success(f"{variable} is set.")
        else:
            console.print(f"{escape(variable)}: not set")
    if not check_package("langchain_anthropic"):
        console.print(f"[dim]The anthropic provider needs langchain-anthropic. {escape(ANTHROPIC_EXTRA_HINT)}[/dim]")

    # 5. The configured engine: built from --config (or NL2SQL_CONFIG, or
    # ./nl2sql.yaml) and checked: each datasource connects, each agent's key
    # resolves, the index is built.
    console.print("\n[bold]Engine:[/bold]")
    _report_engine(ctx)


def _report_engine(ctx: typer.Context) -> None:
    from nl2sql import NL2SQL, EngineNotReady

    try:
        engine = NL2SQL.from_config(config_option(ctx))
    except Exception as exc:
        console.print(Text(str(exc), style="red"))
        return
    try:
        report = engine.check()
        ok = report.ok
    except EngineNotReady as exc:
        report, ok = exc.report, False
    except Exception as exc:
        console.print(Text(f"The engine check failed: {exc}", style="red"))
        return
    finally:
        engine.close()
    source = (engine.config.folder / "nl2sql.yaml").as_posix() if engine.config.folder else "Config"
    console.print(f"Config: {escape(source)}", soft_wrap=True)
    console.print(Text(str(report)))
    if ok:
        print_success("The engine is ready.")
    else:
        print_error("The engine is not fully ready: see above.")
    if report.index != "ok":
        console.print("Build the index: nl2sql index")
