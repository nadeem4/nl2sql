"""``nl2sql launch``: set up a database, an index and a first look in one sitting (wave_c.md, section 7).

``nl2sql launch [DIR] [--provider NAME] [--as ROLE] [--draft] [--yes] [--no-ports]``. A stub: L3 fills it. It is
the one command that reads ``DATABASE_URL`` (the boundary test's allow-list) and reloads the ``.env`` it wrote.
"""
from __future__ import annotations

import pathlib
from typing import List, Optional

import typer
from typing_extensions import Annotated

from nl2sql.cli.common.planned import not_yet


def launch_command(
    ctx: typer.Context,
    directory: Annotated[pathlib.Path, typer.Argument(help="The project folder")] = pathlib.Path("."),
    provider: Annotated[str, typer.Option("--provider", help="The LLM provider, with its default model")] = "openai",
    as_: Annotated[Optional[List[str]], typer.Option(
        "--as", help="The admin role to act as (repeatable). Without one, the first of policies.admin_roles.")] = None,
    draft: Annotated[bool, typer.Option("--draft", help="Also draft descriptions with the model")] = False,
    yes: Annotated[bool, typer.Option(
        "--yes", help="Accept nothing: export the review file for review in git")] = False,
    no_ports: Annotated[bool, typer.Option("--no-ports", help="Do not look for databases on local ports")] = False,
) -> None:
    """Find a database, connect it, index it, and look at what was found."""
    not_yet("nl2sql launch", "L3")