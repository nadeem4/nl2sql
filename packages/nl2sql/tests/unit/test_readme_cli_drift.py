"""The README's CLI reference names every command the Typer app has."""
import pathlib

import typer.main

from nl2sql.cli.main import app

REPO = pathlib.Path(__file__).resolve().parents[4]


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else None]


def _command_paths(group, prefix="nl2sql"):
    for name, command in group.commands.items():
        path = f"{prefix} {name}"
        yield path
        if hasattr(command, "commands"):
            yield from _command_paths(command, path)


def test_every_cli_command_and_subcommand_is_in_the_readme_cli_reference():
    section = _section((REPO / "README.md").read_text(encoding="utf-8"), "CLI reference")
    paths = list(_command_paths(typer.main.get_command(app)))
    assert "nl2sql benchmark retrieval" in paths and "nl2sql feedback export" in paths
    missing = [p for p in paths if f"`{p}" not in section]
    assert not missing, f"add these to the README's CLI reference: {missing}"
