"""``nl2sql curated``: the curated answers file.

* ``validate`` -- check ``curated.yaml`` before it ships.
* ``proposals export`` -- thumbs-up generated answers as proposed verified
  queries, in their own file (or on stdout) for a person to review. It never
  writes ``curated.yaml``.
"""
import os
import pathlib
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typing_extensions import Annotated

from nl2sql import CuratedAPI
from nl2sql.cli.common.decorators import handle_cli_errors

app = typer.Typer(help="Check the curated answers file.", no_args_is_help=True)
console = Console()


@app.command("validate")
@handle_cli_errors
def validate(
    curated: Annotated[Optional[pathlib.Path], typer.Option("--curated", help="Path to curated.yaml")] = None,
    config: Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config")] = None,
    secrets: Annotated[Optional[pathlib.Path], typer.Option("--secrets", help="Path to secrets config")] = None,
):
    """
    Validate curated.yaml: shape, ids, params, read-only SQL in each datasource's
    dialect, and the tables it reads against the indexed schema.
    """
    report = CuratedAPI().validate(curated_path=curated, datasources_path=config, secrets_path=secrets)
    console.print(f"[bold blue]Validating curated answers from:[/bold blue] {escape(report.path)}")

    for error in report.errors:
        console.print(f"[bold red]Error:[/bold red] {escape(error)}")

    if report.entries:
        table = Table(title="Verified Queries")
        table.add_column("Datasource", style="cyan")
        table.add_column("Query", style="magenta")
        table.add_column("Status")
        table.add_column("Details", style="white")
        for entry in report.entries:
            status = "[green]OK[/green]" if entry.status == "OK" else f"[red]{entry.status}[/red]"
            table.add_row(Text(entry.datasource_id), Text(entry.query_id), status, Text(entry.details))
        console.print(table)

    for note in report.notes:
        console.print(f"[yellow]Note:[/yellow] {escape(note)}")

    if not report.ok:
        console.print("\n[bold red]Curated answers file has errors.[/bold red]")
        sys.exit(1)
    console.print("\n[bold green]✓ Curated answers file is valid[/bold green]")


proposals_app = typer.Typer(help="Verified queries proposed from thumbs-up feedback.", no_args_is_help=True)
app.add_typer(proposals_app, name="proposals")


@proposals_app.command("export")
def export_proposals(
    out: Annotated[Optional[pathlib.Path], typer.Option(
        "--out", help="Write the proposals here (default: print them). Never curated.yaml itself.")] = None,
):
    """
    Turn thumbs-up feedback on generated answers into proposed verified queries,
    in curated.yaml's shape, for a person to review and commit.
    """
    from nl2sql.common.settings import settings
    from nl2sql.feedback import FeedbackStore, build_proposals, render_proposals, write_proposals

    protected = [pathlib.Path(settings.curated_config_path)]
    if os.environ.get("CURATED_CONFIG"):
        protected.append(pathlib.Path(os.environ["CURATED_CONFIG"]))
    if out is not None and any(out.resolve() == p.resolve() for p in protected):
        console.print("[bold red]Error:[/bold red] Proposals never go into the curated answers file itself. "
                      "Pick another --out, review the entries, then move them into curated.yaml.", soft_wrap=True)
        raise typer.Exit(2)

    store_path = pathlib.Path(settings.schema_store_path)
    rows = []
    if store_path.exists():
        store = FeedbackStore(store_path)
        try:
            rows = store.list()
        finally:
            store.close()
    document, skipped = build_proposals(rows)
    text = render_proposals(document)
    count = sum(len(ds["verified_queries"]) for ds in document["datasources"].values())
    reasons = ", ".join(f"{n} {reason.replace('_', ' ')}" for reason, n in skipped.items() if n) or "none"

    if out is None:
        typer.echo(text, nl=False)
        Console(stderr=True).print(f"{count} proposed; skipped: {escape(reasons)}.", soft_wrap=True)
        return
    write_proposals(text, out, protected=protected)
    console.print(f"[green]Wrote {count} proposed verified quer{'y' if count == 1 else 'ies'}[/green] to "
                  f"{escape(str(out.resolve()))}; skipped: {escape(reasons)}.", soft_wrap=True)
    console.print("Review each entry, turn literal values into {params}, then move it into curated.yaml.")
