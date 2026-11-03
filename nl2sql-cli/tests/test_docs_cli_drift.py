"""The docs' CLI reference (docs/reference/cli.md) names every command the Typer app has."""
import pathlib

import typer.main

from nl2sql_cli.main import app

REPO = pathlib.Path(__file__).resolve().parents[2]


def _command_paths(group, prefix="nl2sql"):
    for name, command in group.commands.items():
        path = f"{prefix} {name}"
        yield path
        if hasattr(command, "commands"):
            yield from _command_paths(command, path)


def test_every_cli_command_and_subcommand_is_in_the_docs_cli_reference():
    section = (REPO / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    paths = list(_command_paths(typer.main.get_command(app)))
    assert "nl2sql review accept" in paths and "nl2sql feedback export" in paths
    missing = [p for p in paths if f"`{p}" not in section]
    assert not missing, f"add these to docs/reference/cli.md: {missing}"
