"""``nl2sql config check [--connect]``: the whole config, checked without starting the engine.

The checking is ``nl2sql.config.check.check_config``; this prints its result:
the problems grouped by file, every secret as present or missing (never its
value), and with ``--connect`` each datasource's status. Exit code 0 means no
problems and 1 means problems; a usage error is 2. See
docs/architecture/configuration_v2.md, sections 6 and 10.
"""
from __future__ import annotations

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.common.engine import config_option
from nl2sql.cli.console import console

app = typer.Typer(no_args_is_help=True)


@app.command("check")
def check(
    ctx: typer.Context,
    connect: Annotated[bool, typer.Option(
        "--connect", help="Also connect to every datasource; one that fails is a problem.")] = False,
):
    """
    Check the whole config without starting the engine: files, includes, ${env:...}, and every component it names.
    """
    from nl2sql.config.check import check_config

    report = check_config(config_option(ctx), connect=connect)

    def say(text: str, style: str = "") -> None:
        console.print(f"[{style}]{escape(text)}[/{style}]" if style else escape(text), soft_wrap=True)

    for file, problems in report.by_file().items():
        say(file, "bold")
        for problem in problems:
            say(f"  {problem.path or '(the whole file)'}")
            say(f"    {problem.message}")
            if problem.fix:
                say(f"    Fix: {problem.fix}")
        say("")
    if report.secrets:
        say("Secrets:", "bold")
        for secret in report.secrets:
            say(f"  {secret}")
        say("")
    if report.datasources:
        say("Datasources:", "bold")
        for status in report.datasources:  # a failed one's reason is with the problems above
            say(f"  {status.id}: {status.status}")
        say("")

    shown = report.path.as_posix()
    if report.ok:
        say(f"{shown}: no problems.", "green")
        return
    count = len(report.problems)
    files = len(report.by_file())
    say(f"{shown}: {count} {'problem' if count == 1 else 'problems'} "
        f"in {files} {'file' if files == 1 else 'files'}.", "bold red")
    raise typer.Exit(1)
