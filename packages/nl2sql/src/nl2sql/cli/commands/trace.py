"""``nl2sql trace show`` and ``nl2sql trace replay``: debug a run from its trace file."""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

import typer
from rich import box
from rich.markup import escape
from rich.table import Table
from typing_extensions import Annotated

from nl2sql.cli.console import console
from nl2sql.common.settings import settings
from nl2sql.tracing.document import find_trace, load_trace

app = typer.Typer(help="Inspect and replay run traces.", no_args_is_help=True)

_STYLE = {"ok": "green", "warning": "yellow", "error": "red", "unfinished": "red"}


def _resolve(target: str) -> pathlib.Path:
    """A trace file path, or the newest file for a trace id in TRACE_DIR."""
    path = pathlib.Path(target)
    if path.is_file():
        return path
    try:
        found = find_trace(target, pathlib.Path(settings.trace_dir))
    except ValueError:
        found = None
    if found is None:
        console.print(f"[red][ERROR][/red] No trace file or trace id {escape(target)!s} "
                      f"(looked in {escape(str(pathlib.Path(settings.trace_dir).resolve()))}).")
        raise typer.Exit(1)
    return found


def _load(target: str) -> Dict[str, Any]:
    path = _resolve(target)
    try:
        return load_trace(path)
    except ValueError as exc:
        console.print(f"[red][ERROR][/red] {escape(str(exc))}")
        raise typer.Exit(1)


def _tokens(node: Dict[str, Any]) -> int:
    return sum(((c.get("usage") or {}).get("total_tokens") or 0) for c in node.get("llm_calls") or [])


def _first_error(node: Dict[str, Any]) -> str:
    if node.get("exception"):
        return node["exception"]
    for item in (node.get("errors") or []) + (node.get("warnings") or []):
        if isinstance(item, dict):
            code = item.get("error_code")
            return f"{code}: {item.get('message', '')}" if code else str(item.get("message", item))
        return str(item)
    for call in node.get("llm_calls") or []:
        if call.get("error"):
            return call["error"]
    return ""


def _earliest(nodes: List[Dict[str, Any]], statuses) -> Optional[Dict[str, Any]]:
    """The execution with one of ``statuses`` that finished first."""
    hits = [n for n in nodes if n.get("status") in statuses]
    return min(hits, key=lambda n: n.get("end_seq") or n.get("seq") or 0) if hits else None


def first_failure(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The failing node execution that finished first."""
    return _earliest(nodes, ("error", "unfinished"))


@app.command("show")
def show(target: Annotated[str, typer.Argument(help="A trace file, or a trace id in TRACE_DIR")]):
    """Print a run's timeline: each node, attempt, duration, tokens, status and errors."""
    doc = _load(target)
    request = doc.get("request") or {}
    nodes = doc.get("nodes") or []
    first = first_failure(nodes)
    label = "First failure"
    if first is None:
        # A run that recovered (a retry, say) has no failing node, but the node
        # whose warning set it off is where to look.
        first, label = _earliest(nodes, ("warning",)), "First warning"

    console.print(f"[bold]Trace[/bold] {escape(str(doc.get('trace_id')))}  "
                  f"[dim]{escape(str(doc.get('started_at', '')))}[/dim]")
    console.print(f"Question: {escape(str(request.get('question', '')))}")
    if doc.get("failed"):
        console.print("[yellow]Needs a look:[/yellow] the run reported errors, needed a retry, or did not complete.")
    console.print(f"Roles: {escape(', '.join(request.get('roles') or []))}   execute: {request.get('execute')}   "
                  f"outcome: {escape(str(doc.get('outcome')))}   result: "
                  f"{escape(str((doc.get('result') or {}).get('status') or '-'))}")

    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    for col, justify in (("#", "right"), ("node", "left"), ("sub-query", "left"), ("try", "right"),
                         ("time", "right"), ("tokens", "right"), ("status", "left")):
        table.add_column(col, justify=justify, no_wrap=True)
    problems = []
    for index, node in enumerate(nodes, start=1):
        status = node.get("status", "")
        name = ("  " if node.get("parent") else "") + str(node.get("node"))
        tokens = _tokens(node)
        duration = node.get("duration_s")
        seq = (">" if node is first else "") + str(index)
        table.add_row(
            seq, escape(name), escape(str(node.get("sub_query_id") or "")), str(node.get("attempt", "")),
            f"{duration:.3f}s" if isinstance(duration, (int, float)) else "-",
            f"{tokens:,}" if tokens else "", f"[{_STYLE.get(status, 'white')}]{escape(status)}[/]",
        )
        message = _first_error(node)
        if message:
            problems.append((seq, node.get("node"), status, message))
    console.print(table)
    for seq, name, status, message in problems:
        console.print(f"  [{_STYLE.get(status, 'white')}]#{escape(seq.lstrip('>'))} {escape(str(name))}[/]: "
                      f"{escape(message[:300])}", highlight=False)
    if first is not None:
        console.print(f"[red]{label} (marked >):[/red] {escape(str(first.get('node')))} "
                      f"(attempt {first.get('attempt')}): {escape(_first_error(first)[:300])}")
    else:
        console.print("[green]No node failed.[/green]")


def replay_command(target: str, ctx_factory) -> None:
    """Re-runs the traced question with the recorded LLM answers; see ``nl2sql.tracing.replay``."""
    from nl2sql.tracing.replay import replay_trace, unreachable_datasources

    doc = _load(target)
    request = doc.get("request") or {}
    console.print(f"Replaying trace {escape(str(doc.get('trace_id')))}: {escape(str(request.get('question', '')))}")
    ctx = ctx_factory()

    problems = unreachable_datasources(doc, ctx)
    if problems:
        console.print("[red][ERROR][/red] Replay runs the real pipeline against the same datasource, "
                      "and it is not reachable here:")
        for problem in problems:
            console.print(f"  - {escape(problem)}")
        console.print("Run from the project the trace was recorded in (same --env and configs).")
        raise typer.Exit(1)

    report = replay_trace(doc, ctx)
    console.print(f"{report.served} recorded LLM calls served; 0 model calls made.")

    if report.divergence is not None:
        d = report.divergence
        console.print(f"[yellow]Replay diverged at {escape(d.where())}[/yellow]")
        console.print(f"  {escape(d.describe())}.")
        if d.detail:
            console.print(d.detail, markup=False, highlight=False)
        console.print("Everything before that node ran as recorded. If a prompt changed on purpose, "
                      "the recording is stale: one real run is needed to record a new trace.")
        raise typer.Exit(1)

    result = report.result
    console.print(f"Result: {escape(str(result.get('status') or '-'))}")
    for sub in result.get("sub_queries") or []:
        if sub.get("sql"):
            console.print(f"SQL ({escape(str(sub.get('id')))}): {escape(sub['sql'])}", highlight=False)
    if report.differences:
        console.print(f"[yellow]The replayed result differs from the recording in "
                      f"{len(report.differences)} place(s):[/yellow]")
        for change in report.differences[:50]:
            console.print(f"  - {escape(change)}", highlight=False)
        raise typer.Exit(1)
    console.print("[green]The replayed result matches the recording.[/green]")
