import importlib.util

from rich.table import Table
from rich.text import Text

from nl2sql.cli.console import console
from nl2sql.configs import ConfigManager
from nl2sql.datasources import DatasourceRegistry
from nl2sql.secrets import SecretManager


def check_package(name: str) -> bool:
    """Checks if a python package is installed."""
    import_name = name.replace("-", "_")
    return importlib.util.find_spec(import_name) is not None


def verify_connectivity(print_table: bool = True) -> bool:
    """Checks connectivity for every configured datasource.

    This backs ``nl2sql doctor``, which is what a user runs when something is
    already wrong, so no failure mode may escape as an exception: a broken
    config, an uninstalled driver, an unresolvable secret and a driver that
    raises on connect all have to come back as a reported row or a message.

    Returns:
        bool: True only if every datasource connected. ``setup`` branches on
        this to decide whether to warn before continuing.
    """
    try:
        config_manager = ConfigManager()
        secret_manager = SecretManager()
        secret_configs = config_manager.load_secrets()
        if secret_configs:
            secret_manager.configure(secret_configs)
        ds_configs = config_manager.load_datasources()
        registry = DatasourceRegistry(secret_manager)
    except Exception as e:
        # Externally-sourced text (paths, parser errors): print it as Text so
        # a bracket sequence in the message cannot be parsed as rich markup.
        console.print(Text(f"Connectivity check failed: {e}", style="red"))
        return False

    if not ds_configs:
        console.print("[yellow]No datasources configured.[/yellow]")
        return True

    results = []

    with console.status("[bold green]Verifying connectivity...[/bold green]"):
        for config in ds_configs:
            ds_id = getattr(config, "id", None) or "<unknown>"
            try:
                adapter = registry.register_datasource(config)
            except Exception as e:
                results.append((ds_id, False, str(e)))
                continue

            try:
                ok = bool(adapter.test_connection())
                details = "" if ok else "Connection test failed."
            except Exception as e:
                ok, details = False, str(e)
            results.append((ds_id, ok, details))

    all_ok = all(ok for _, ok, _ in results)

    if print_table:
        conn_table = Table(show_header=True, header_style="bold cyan")
        conn_table.add_column("Datasource ID")
        conn_table.add_column("Status")
        conn_table.add_column("Details")

        for ds_id, ok, details in results:
            status = "[green]OK[/green]" if ok else "[red]Failed[/red]"
            conn_table.add_row(Text(str(ds_id)), status, Text(str(details)))

        console.print(conn_table)

    return all_ok
