"""``nl2sql curated``: the curated answers file.

* ``validate`` -- check the ``curated`` section before it ships.
* ``test`` -- run every verified query once against its database.
* ``proposals export`` -- thumbs-up generated answers as proposed verified
  queries, in their own file (or on stdout) for a person to review. It never
  writes the config's own files.
"""
import pathlib
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from typing_extensions import Annotated

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.common.engine import build_engine, load_project_config

app = typer.Typer(help="Check and test the curated answers file.", no_args_is_help=True)
console = Console()


@app.command("validate")
@handle_cli_errors
def validate(ctx: typer.Context):
    """
    Validate the curated section: shape, ids, params, read-only SQL in each
    datasource's dialect, and the tables it reads against the indexed schema.
    """
    report = build_engine(ctx, check=False).curated.validate()
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


@app.command("test")
@handle_cli_errors
def test(ctx: typer.Context):
    """
    Run every verified query once against its database, read-only, with each
    param's default or example bound. Exits 1 if any query fails.
    """
    report = build_engine(ctx, check=False).curated.test()
    console.print(f"[bold blue]Testing verified queries from:[/bold blue] {escape(report.path)}")

    for error in report.errors:
        console.print(f"[bold red]Error:[/bold red] {escape(error)}")

    if report.entries:
        table = Table(title="Verified Queries")
        table.add_column("Datasource", style="cyan")
        table.add_column("Query", style="magenta")
        table.add_column("Status")
        table.add_column("Params", style="white")
        table.add_column("Details", style="white")
        for entry in report.entries:
            status = "[green]PASS[/green]" if entry.status == "PASS" else f"[red]{entry.status}[/red]"
            params = ", ".join(f"{k}={v}" for k, v in entry.params.items())
            table.add_row(Text(entry.datasource_id), Text(entry.query_id), status, Text(params), Text(entry.details))
        console.print(table)

    if not report.ok:
        console.print("\n[bold red]Some verified queries failed.[/bold red]")
        sys.exit(1)
    console.print(f"\n[bold green]✓ {len(report.entries)} verified queries ran[/bold green]")


proposals_app = typer.Typer(help="Verified queries proposed from thumbs-up feedback.", no_args_is_help=True)
app.add_typer(proposals_app, name="proposals")


@proposals_app.command("export")
def export_proposals(
    ctx: typer.Context,
    out: Annotated[Optional[pathlib.Path], typer.Option(
        "--out", help="Write the proposals here (default: print them). Never a file of the config itself.")] = None,
):
    """
    Turn thumbs-up feedback on generated answers into proposed verified queries,
    in the curated section's shape, for a person to review and commit.
    """
    from nl2sql.feedback import build_proposals, render_proposals, write_proposals

    config = load_project_config(ctx)
    protected = _config_files(config.folder)
    if out is not None and any(out.resolve() == p.resolve() for p in protected):
        console.print("[bold red]Error:[/bold red] Proposals never go into the config's own files. "
                      "Pick another --out, review the entries, then move them into the curated section.",
                      soft_wrap=True)
        raise typer.Exit(2)

    with build_engine(ctx, check=False) as engine:
        rows = engine.feedback.list()
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
    console.print("Review each entry, turn literal values into {params}, then move it into the curated section.")


def _config_files(folder: Optional[pathlib.Path]) -> list:
    """``nl2sql.yaml`` and every file it includes, so proposals never overwrite the config."""
    if folder is None:
        return []
    import yaml

    toc = folder / "nl2sql.yaml"
    files = [toc]
    try:
        sections = yaml.safe_load(toc.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return files
    for value in sections.values() if isinstance(sections, dict) else []:
        if isinstance(value, dict) and isinstance(value.get("file"), str):
            target = folder / value["file"]
            files += sorted(target.glob("*.yaml")) if target.is_dir() else [target]
    return files
