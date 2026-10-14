"""``nl2sql init``: write a new config folder, non-interactively.

The engine writes and checks the files (``nl2sql.config.init_project``); this
command prints what was written and what to do next.
"""
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from nl2sql.config import init_project
from nl2sql.llm.providers import env_var_for_provider


def init_command(directory: Path, demo: bool, provider: str, force: bool) -> None:
    console = Console(soft_wrap=True)  # paths stay whole, so they can be copied
    try:
        written = init_project(directory, demo=demo, provider=provider, force=force)
    except FileExistsError as exc:
        console.print(f"[red]{escape(str(exc))} Run again with --force to overwrite them.[/red]")
        raise typer.Exit(1)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1)

    for path in written:
        console.print(f"[green]wrote[/green] {escape(path.as_posix())}")

    config = (directory / "nl2sql.yaml").as_posix()
    index = "nl2sql index" if directory == Path(".") else f"nl2sql --config {config} index"
    steps = [] if demo else [f"Point {(directory / 'datasources' / 'example.yaml').as_posix()} at your database."]
    steps += [
        f"Set {env_var_for_provider(provider)}, in the environment or in a .env file next to {config}.",
        f"Build the index: {index}",
    ]
    console.print("\nNext steps:")
    for number, step in enumerate(steps, 1):
        console.print(f"  {number}. {escape(step)}")
