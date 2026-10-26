"""``nl2sql profile``: the schema profiler (docs/architecture/wave_b.md, section 6).

``nl2sql profile [--datasource ID ...] --as ROLE`` calls ``engine.knowledge.profile`` as the caller ``--as``
(repeatable) and ``--user`` build, prints each datasource's report, then the open proposals it left in the
review queue, as ``nl2sql review list`` shows them. P2 adds ``--draft`` and ``--max-cost``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.commands.knowledge import AsOption, UserOption, caller
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

if TYPE_CHECKING:
    from nl2sql import NL2SQL, UserContext
    from nl2sql.profiler.models import ProfileReport

def print_reports(engine: "NL2SQL", who: "UserContext", reports: List["ProfileReport"]) -> None:
    """Each report, then the open proposals of the profiled datasources that the profiler filed."""
    if not reports:
        console.print("No datasource this caller can read to profile.", soft_wrap=True)
        return
    for report in reports:
        counts = ", ".join(f"{n} {kind}" for kind, n in report.proposals.items()) or "nothing new"
        bounds = "" if report.stopped_by == "done" else f"; stopped by {report.stopped_by}"
        console.print(f"[bold]{escape(report.datasource_id)}[/bold]: {escape(counts)}; {report.probes} overlap "
                      f"probes, {report.skipped_by_budget} candidates proposed without one{escape(bounds)} "
                      f"[dim]({report.seconds:.1f}s)[/dim]", soft_wrap=True)
        for issue in report.issues:
            console.print(f"  [yellow]issue:[/yellow] {escape(issue)}", soft_wrap=True)
    profiled = {r.datasource_id for r in reports if r.schema_version}
    waiting = [p for p in engine.proposals.list(user_context=who) if p.origin == "profiler"
               and p.datasource_id in profiled]
    if waiting:
        console.print("\n[bold]Waiting for review[/bold] (nl2sql review list)")
    for p in waiting:
        console.print(f"{escape(p.id)}  {escape(p.kind)}  {escape(p.datasource_id)}  {escape(p.target)}  "
                      f"[dim]{escape(str(p.change))}[/dim]", soft_wrap=True)


def caller_or_exit(engine: "NL2SQL", roles: Optional[List[str]], user: Optional[str]) -> "UserContext":
    """The caller ``--as`` and ``--user`` build; exit 1 (closing the engine) on an unknown role or none."""
    try:
        return caller(engine, list(roles or []), user)
    except ValueError as error:
        engine.close()
        console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
        raise typer.Exit(1)


def profile_datasources(engine: "NL2SQL", who: "UserContext", datasource_ids: Optional[List[str]]) -> None:
    """Profile as ``who`` and print the result; exit 1 on an unknown datasource."""
    try:
        reports = engine.knowledge.profile(datasource_ids or None, user_context=who)
    except ValueError as error:
        console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
        raise typer.Exit(1)
    print_reports(engine, who, reports)


def profile_command(
    ctx: typer.Context,
    datasource: Annotated[Optional[List[str]], typer.Option(
        "--datasource", "-d", help="Only this datasource (repeatable). Without one, every datasource the caller reaches.",
    )] = None,
    as_: AsOption = None,
    user: UserOption = None,
) -> None:
    """
    Probe the indexed schema for joins the database does not declare and for column roles, and file them as proposals.
    """
    engine = build_engine(ctx, check=False)
    with engine:
        profile_datasources(engine, caller_or_exit(engine, as_, user), datasource)
