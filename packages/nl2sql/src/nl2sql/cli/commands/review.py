"""``nl2sql review``: the review queue (docs/architecture/wave_b.md, section 5).

* ``list``   -- the proposals the caller may see, newest first.
* ``show``   -- one proposal, with the current glossary or curated value beside the proposed change.
* ``accept`` -- accept ids (or ``--all`` the open ones of a kind or datasource); writes each file once.
* ``reject`` -- reject ids, with an optional ``--reason``.
* ``export`` -- the open proposals the caller may see, as a review file.
* ``apply``  -- read a review file: accept each ``accept``, reject each ``reject``.

Each calls the matching ``engine.proposals`` method as the caller ``--as``
(repeatable) and ``--user`` build, as ``nl2sql knowledge`` does. Deciding needs
an admin role (``policies.admin_roles``). A refusal exits 1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.commands.knowledge import AsOption, UserOption, caller
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

if TYPE_CHECKING:
    from nl2sql import AcceptReport

app = typer.Typer(help="The review queue: `list`, `show`, `accept`, `reject`, `export`, `apply`.",
                  no_args_is_help=True)

KindOption = Annotated[Optional[str], typer.Option(
    "--kind", help="Only this kind: join, column, table, term or verified_query.")]
DatasourceOption = Annotated[Optional[str], typer.Option("--datasource", help="Only this datasource.")]


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}", soft_wrap=True)
    raise typer.Exit(1)


def _engine_and_caller(ctx: typer.Context, roles: Optional[List[str]], user: Optional[str]):
    # Nothing here connects to a datasource, so the engine is not checked.
    engine = build_engine(ctx, check=False)
    try:
        return engine, caller(engine, list(roles or []), user)
    except ValueError as error:
        engine.close()
        _fail(str(error))


def _run(call):
    """``call()``, a refusal printed with its fix and exit 1."""
    from nl2sql import ReviewError

    try:
        return call()
    except (ReviewError, ValueError) as error:
        _fail(str(error))


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _print_report(report: "AcceptReport") -> None:
    for proposal in report.accepted:
        console.print(f"[green]accepted[/green] {escape(proposal.id)} {escape(proposal.kind)} "
                      f"{escape(proposal.target)}", soft_wrap=True)
    for proposal in report.rejected:
        console.print(f"[yellow]rejected[/yellow] {escape(proposal.id)} {escape(proposal.kind)} "
                      f"{escape(proposal.target)}", soft_wrap=True)
    for path in report.files_written:
        console.print(f"[green]wrote[/green] {escape(path)}", soft_wrap=True)
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {escape(warning)}", soft_wrap=True)
    for proposal_id, reason in report.refused.items():
        console.print(f"[red]refused[/red] {escape(proposal_id)}: {escape(reason)}", soft_wrap=True)


@app.command("list")
def list_command(ctx: typer.Context, kind: KindOption = None, datasource: DatasourceOption = None,
                 status: Annotated[str, typer.Option(
                     "--status", help="proposed (default), accepted, rejected, superseded, or all.")] = "proposed",
                 as_: AsOption = None, user: UserOption = None) -> None:
    """The proposals the caller may see, newest first."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        proposals = engine.proposals.list(user_context=who, kind=kind, datasource_id=datasource,
                                          status=None if status == "all" else status)
    if not proposals:
        console.print("No proposals.", soft_wrap=True)
    for p in proposals:
        console.print(f"{escape(p.id)}  {escape(p.status)}  {escape(p.kind)}  {escape(p.datasource_id)}  "
                      f"{escape(p.target)}  [dim]({escape(p.origin)})[/dim]", soft_wrap=True)


@app.command("show")
def show_command(ctx: typer.Context, proposal_id: Annotated[str, typer.Argument(help="The proposal's id.")],
                 as_: AsOption = None, user: UserOption = None) -> None:
    """One proposal, with the current value beside the proposed change."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        proposal = _run(lambda: engine.proposals.get(proposal_id, user_context=who))
        current = engine.proposals.current(proposal_id, user_context=who)
    console.print(f"[bold]{escape(proposal.id)}[/bold] {escape(proposal.status)} {escape(proposal.kind)} "
                  f"in {escape(proposal.datasource_id)}: {escape(proposal.target)}", soft_wrap=True)
    console.print(f"  origin: {escape(proposal.origin)}; created {proposal.created_at.isoformat(timespec='seconds')}"
                  f"{' by ' + escape(proposal.created_by) if proposal.created_by else ''}", soft_wrap=True)
    console.print(f"  current:  {escape(_compact(current)) if current is not None else '(none you can see)'}",
                  soft_wrap=True)
    console.print(f"  proposed: {escape(_compact(proposal.change))}", soft_wrap=True)
    if proposal.evidence:
        console.print(f"  evidence: {escape(_compact(proposal.evidence))}", soft_wrap=True)
    if proposal.decided_at:
        console.print(f"  decided {proposal.decided_at.isoformat(timespec='seconds')} by "
                      f"{escape(proposal.decided_by or '?')}"
                      f"{'; wrote ' + escape(proposal.wrote) if proposal.wrote else ''}"
                      f"{'; reason: ' + escape(proposal.reason) if proposal.reason else ''}", soft_wrap=True)


@app.command("accept")
def accept_command(ctx: typer.Context,
                   ids: Annotated[Optional[List[str]], typer.Argument(help="The ids to accept.")] = None,
                   all_: Annotated[bool, typer.Option(
                       "--all", help="Every open proposal the caller sees (narrow with --kind, --datasource).")
                   ] = False,
                   kind: KindOption = None, datasource: DatasourceOption = None,
                   as_: AsOption = None, user: UserOption = None) -> None:
    """Accept proposals: the glossary and curated.yaml are each written once."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        chosen = list(ids or [])
        if all_:
            chosen += [p.id for p in engine.proposals.list(user_context=who, kind=kind, datasource_id=datasource)]
        if not chosen:
            _fail("Name the ids to accept, or pass --all.")
        report = _run(lambda: engine.proposals.accept(chosen, by=who))
    _print_report(report)
    if report.refused:
        raise typer.Exit(1)


@app.command("reject")
def reject_command(ctx: typer.Context, ids: Annotated[List[str], typer.Argument(help="The ids to reject.")],
                   reason: Annotated[Optional[str], typer.Option("--reason", help="Why, kept with each.")] = None,
                   as_: AsOption = None, user: UserOption = None) -> None:
    """Reject proposals; a rejected change is never proposed again."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        rejected = _run(lambda: engine.proposals.reject(ids, by=who, reason=reason))
    for proposal in rejected:
        console.print(f"[yellow]rejected[/yellow] {escape(proposal.id)} {escape(proposal.kind)} "
                      f"{escape(proposal.target)}", soft_wrap=True)


@app.command("export")
def export_command(ctx: typer.Context,
                   out: Annotated[Path, typer.Option("--out", help="Write the review file here.")],
                   kind: KindOption = None, datasource: DatasourceOption = None,
                   as_: AsOption = None, user: UserOption = None) -> None:
    """The open proposals the caller may see, as a review file to decide in a pull request."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        count = _run(lambda: engine.proposals.export(out, user_context=who, kind=kind, datasource_id=datasource))
    console.print(f"[green]wrote[/green] {count} proposal{'' if count == 1 else 's'} to {escape(out.as_posix())}",
                  soft_wrap=True)


@app.command("apply")
def apply_command(ctx: typer.Context, path: Annotated[Path, typer.Argument(help="A review file.")],
                  as_: AsOption = None, user: UserOption = None) -> None:
    """Read a review file: accept each `accept` (with its edited change), reject each `reject`."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        report = _run(lambda: engine.proposals.apply(path, by=who))
    _print_report(report)
    if report.refused:
        raise typer.Exit(1)
