"""``nl2sql feedback``: the playground's answer ratings, the guardrail rates, and draft gold entries.

* ``list``   -- the ratings, newest first.
* ``stats``  -- rates over the ratings plus any run traces in ``<storage.path>/traces``.
* ``export --good`` -- thumbs-up runs as draft gold entries in their own YAML file.
* ``clear``  -- empty the feedback table; schema snapshots and the plan cache stay.

Every command reads the ``feedback`` table of the state store,
``<storage.path>/state.db``. None of them creates the store when it is missing.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional

import typer
from rich import box
from rich.markup import escape
from rich.table import Table
from typing_extensions import Annotated

from nl2sql.cli.console import console
from nl2sql.cli.common.engine import store_paths
from nl2sql.evaluation.gold import GOLD_DATASET_PATH
from nl2sql.feedback import FeedbackStore, compute_stats, draft_entries, load_traces, write_drafts

app = typer.Typer(help="Answer feedback from the playground, and guardrail rates over recorded runs.",
                  no_args_is_help=True)

DEFAULT_DRAFTS = pathlib.Path("feedback_gold_drafts.yaml")


def _store_path(ctx: typer.Context) -> pathlib.Path:
    return store_paths(ctx).state


def _rows(ctx: typer.Context) -> List[Dict[str, Any]]:
    path = _store_path(ctx)
    if not path.exists():
        return []
    store = FeedbackStore(path)
    try:
        return store.list()
    finally:
        store.close()


def _pct(rate: Optional[float]) -> str:
    return "-" if rate is None else f"{rate * 100:.1f}%"


@app.command("list")
def list_command(ctx: typer.Context,
                 limit: Annotated[int, typer.Option(help="Show at most this many ratings.")] = 50) -> None:
    """The ratings, newest first: question, role, rating, note and status."""
    rows = _rows(ctx)[:limit]
    if not rows:
        console.print(f"No feedback recorded in {escape(str(_store_path(ctx).resolve()))}.", soft_wrap=True)
        return
    for row in rows:
        rating = "[green]up  [/green]" if row["rating"] == "up" else "[red]down[/red]"
        note = f"  note: {escape(row['note'])}" if row["note"] else ""
        console.print(f"{rating}  {escape(row['question'])}  [dim](as {escape(row['role'])}, "
                      f"{escape(row['status'] or 'no status')}, trace {escape(row['trace_id'])})[/dim]{note}",
                      soft_wrap=True)


@app.command("stats")
def stats_command(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Print the numbers as JSON.")] = False,
    traces: Annotated[Optional[pathlib.Path], typer.Option(
        "--traces", help="Directory of run traces to include (default: <storage.path>/traces).")] = None,
) -> None:
    """Rates over recorded runs: feedback, refusals, refiner retries, validator failures, errors, plan cache."""
    trace_dir = traces if traces is not None else store_paths(ctx).traces
    stats = compute_stats(_rows(ctx), load_traces(trace_dir))
    if as_json:
        typer.echo(json.dumps(stats, indent=2))
        return
    runs = stats["runs"]
    console.print(f"[bold]{runs} runs[/bold], {stats['rated']} rated "
                  f"(feedback table plus traces in {escape(str(trace_dir))})")
    if not runs:
        console.print("Nothing recorded yet. Rate answers in the playground, or set settings.traces.mode: always.")
        return
    fb = stats["feedback"]
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("Signal")
    table.add_column("Runs", justify="right")
    table.add_column("Rate", justify="right")
    table.add_row("Thumbs up", str(fb["up"]), f"{_pct(fb['up_rate'])} of rated")
    table.add_row("Thumbs down", str(fb["down"]), f"{_pct(fb['down_rate'])} of rated")
    refusals = stats["refusals"]
    table.add_row("Refused", str(refusals["runs"]), _pct(refusals["rate"]))
    for code, n in refusals["by_code"].items():
        table.add_row(f"  {code}", str(n), _pct(n / runs))
    retries = stats["refiner_retries"]
    table.add_row(f"Refiner retried ({retries['retries']} retries)", str(retries["runs"]), _pct(retries["rate"]))
    failures = stats["validator_failures"]
    table.add_row(f"Validator rejected a plan ({failures['failures']} times)", str(failures["runs"]),
                  _pct(failures["rate"]))
    errors = stats["errors"]
    table.add_row("Errored", str(errors["runs"]), _pct(errors["rate"]))
    for code, n in errors["by_code"].items():
        table.add_row(f"  {code}", str(n), _pct(n / runs))
    cache = stats["plan_cache"]
    table.add_row(f"Plan cache hits ({cache['hits']} of {cache['sub_queries']} sub-queries)", str(cache["hits"]),
                  _pct(cache["hit_rate"]))
    console.print(table)


@app.command("export")
def export_command(
    ctx: typer.Context,
    good: Annotated[bool, typer.Option("--good", help="Export the thumbs-up runs.")] = False,
    out: Annotated[pathlib.Path, typer.Option("--out", help="Where to write the drafts.")] = DEFAULT_DRAFTS,
) -> None:
    """Write thumbs-up runs as draft gold entries to a separate file, for a person to review."""
    if not good:
        console.print("[red][ERROR][/red] Say which runs to export: --good (the thumbs-up runs).")
        raise typer.Exit(2)
    if out.resolve() == GOLD_DATASET_PATH.resolve():
        console.print("[red][ERROR][/red] Drafts never go into the gold set itself. Pick another --out.")
        raise typer.Exit(2)
    drafts, skipped = draft_entries(_rows(ctx))
    write_drafts(drafts, out)
    console.print(f"[green]Wrote {len(drafts)} draft{'s' if len(drafts) != 1 else ''}[/green] to "
                  f"{escape(str(out.resolve()))}; skipped {skipped} (no SQL, or more than one statement).")
    console.print("Review each entry, then add it to the gold set and run `python -m nl2sql.evaluation.gold`.")


@app.command("clear")
def clear_command(ctx: typer.Context,
                  yes: Annotated[bool, typer.Option("--yes", help="Do not ask to confirm.")] = False) -> None:
    """Delete every rating. Schema snapshots and the plan cache are kept."""
    path = _store_path(ctx)
    if not path.exists():
        console.print(f"No feedback: {escape(str(path.resolve()))} does not exist.")
        return
    if not yes and not typer.confirm(f"Delete every rating in {path.resolve()}?"):
        raise typer.Exit(1)
    store = FeedbackStore(path)
    try:
        removed = store.clear()
    finally:
        store.close()
    console.print(f"[green]Cleared {removed} ratings[/green] from {escape(str(path.resolve()))}.")
