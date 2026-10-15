"""``nl2sql knowledge``: the glossary as the engine uses it (docs/architecture/wave_b.md, section 3).

* ``show``    -- the merged knowledge, each item with its origin; problems last, exit 1 when there are any.
* ``export``  -- the knowledge the caller may see as one canonical ``glossary.yaml``, to stdout or ``--out``.
* ``sources`` -- each knowledge source: its type, item counts, hash, when it loaded and its last error.

Each calls the matching ``engine.knowledge`` method as the caller ``--as``
(repeatable) and ``--user`` build. Without ``--as`` the role is
``policies.default_role``; with neither the command refuses, since a caller
with no role reads nothing (Wave A D33, Wave B D60).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

if TYPE_CHECKING:
    from nl2sql import NL2SQL, UserContext
    from nl2sql.knowledge.models import DatasourceKnowledgeView

app = typer.Typer(help="The glossary as the engine uses it: `show`, `export`, `sources`.", no_args_is_help=True)

AsOption = Annotated[Optional[List[str]], typer.Option(
    "--as", help="A role to read as (repeatable). Without one, policies.default_role.")]
UserOption = Annotated[Optional[str], typer.Option("--user", help="The caller's user id.")]


def caller(engine: "NL2SQL", roles: List[str], user: Optional[str]) -> "UserContext":
    """The ``UserContext`` the command reads as: ``roles``, else the default role.

    Under ``allow_all`` no role is needed. Raises ``ValueError`` for a role
    the policies do not define, or for no role and no default role.
    """
    from nl2sql import UserContext

    policies = engine.config.policies
    unknown = [role for role in roles if role not in policies.roles]
    if unknown:
        defined = ", ".join(policies.roles) or "none (policies.allow_all is set)"
        raise ValueError(f"Unknown role(s) {', '.join(unknown)}. The roles are: {defined}.")
    if not policies.allow_all:
        roles = roles or ([policies.default_role] if policies.default_role else [])
        if not roles:
            raise ValueError("Pass --as <role> (repeatable), or set policies.default_role in nl2sql.yaml. "
                             "A caller with no role reads nothing.")
    return UserContext(user_id=user, roles=roles)


def _engine_and_caller(ctx: typer.Context, roles: Optional[List[str]], user: Optional[str]):
    # Nothing here connects to a datasource, so the engine is not checked.
    engine = build_engine(ctx, check=False)
    try:
        return engine, caller(engine, list(roles or []), user)
    except ValueError as error:
        engine.close()
        console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
        raise typer.Exit(1)


def _origin(view: "DatasourceKnowledgeView", key: str) -> str:
    origin = view.origins.get(key)
    if origin is None:
        return ""
    source = view.sources.get(key)
    return f" [dim]({origin}{': ' + source if source else ''})[/dim]"


def _print(view: "DatasourceKnowledgeView") -> None:
    k = view.knowledge
    console.print(f"[bold]{escape(view.datasource_id)}[/bold] [dim]digest {(view.digest or '')[:12]}[/dim]",
                  soft_wrap=True)
    for name, table in k.tables.items():
        for field in ("description", "notes"):
            value = getattr(table, field)
            if value:
                console.print(f"  table {escape(name)} {field}: {escape(value)}"
                              f"{_origin(view, f'tables.{name}.{field}')}", soft_wrap=True)
    for name, column in k.columns.items():
        for field, value in column.model_dump(exclude_defaults=True).items():
            console.print(f"  column {escape(name)} {field}: {escape(str(value))}"
                          f"{_origin(view, f'columns.{name}.{field}')}", soft_wrap=True)
    for join in k.joins:
        sides = [s if isinstance(s, str) else ",".join(s) for s in (join.from_, join.to)]
        console.print(f"  join {escape(sides[0])} -> {escape(sides[1])} ({join.cardinality})"
                      f"{_origin(view, f'joins.{sides[0]}->{sides[1]}')}", soft_wrap=True)
    for term in k.terms:
        synonyms = f" ({', '.join(term.synonyms)})" if term.synonyms else ""
        console.print(f"  term {escape(term.term)}{escape(synonyms)}: {escape(term.definition or '')}"
                      f"{_origin(view, f'terms.{term.term}')}", soft_wrap=True)
    for rule in k.rules:
        tables = f" [tables: {', '.join(rule.tables)}]" if rule.tables else ""
        console.print(f"  rule {escape(rule.id)}: {escape(rule.text)}{escape(tables)}"
                      f"{_origin(view, f'rules.{rule.id}')}", soft_wrap=True)
    for example in k.examples:
        console.print(f"  example: {escape(example)}{_origin(view, f'examples.{example}')}", soft_wrap=True)


@app.command("show")
def show_command(ctx: typer.Context,
                 datasource: Annotated[Optional[str], typer.Option("--datasource", help="Only this datasource.")] = None,
                 as_: AsOption = None, user: UserOption = None) -> None:
    """The merged knowledge as the engine uses it, each item with its origin; problems last, exit 1 if any."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        try:
            views = engine.knowledge.show(datasource, user_context=who)
        except ValueError as error:
            console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
            raise typer.Exit(1)
    if not views:
        console.print("No datasource this caller can read has knowledge to show.", soft_wrap=True)
    for view in views:
        _print(view)
    problems = [(view.datasource_id, problem) for view in views for problem in view.problems]
    if problems:
        console.print("\n[bold yellow]Problems[/bold yellow]")
        for ds_id, problem in problems:
            console.print(f"  {escape(ds_id)}: {escape(problem)}", soft_wrap=True)
        raise typer.Exit(1)


@app.command("export")
def export_command(ctx: typer.Context,
                   datasource: Annotated[Optional[List[str]], typer.Option(
                       "--datasource", help="Only this datasource (repeatable).")] = None,
                   out: Annotated[Optional[Path], typer.Option("--out", help="Write the glossary to this file.")] = None,
                   as_: AsOption = None, user: UserOption = None) -> None:
    """The knowledge the caller may see as one canonical glossary.yaml, to stdout or --out."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        try:
            text = engine.knowledge.export(out, datasource_ids=datasource or None, user_context=who)
        except ValueError as error:
            console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
            raise typer.Exit(1)
    if out is None:
        typer.echo(text, nl=False)
    else:
        console.print(f"[green]wrote[/green] {escape(out.as_posix())}", soft_wrap=True)


@app.command("sources")
def sources_command(ctx: typer.Context, as_: AsOption = None, user: UserOption = None) -> None:
    """Each knowledge source: its type, item counts per datasource, hash, when it loaded and its last error."""
    engine, who = _engine_and_caller(ctx, as_, user)
    with engine:
        infos = engine.knowledge.sources(user_context=who)
    if not infos:
        console.print("No knowledge sources: the config has no `knowledge` section.", soft_wrap=True)
    for info in infos:
        loaded = info.loaded_at.isoformat(timespec="seconds") if info.loaded_at else "never"
        console.print(f"[bold]{escape(info.type)}[/bold] [dim]sha256 {(info.sha256 or '-')[:12]}, "
                      f"loaded {loaded}[/dim]", soft_wrap=True)
        for ds_id, counts in info.counts.items():
            listed = ", ".join(f"{n} {kind}" for kind, n in counts.items() if n) or "nothing"
            console.print(f"  {escape(ds_id)}: {escape(listed)}", soft_wrap=True)
        if info.error:
            console.print(f"  [red]last reload failed:[/red] {escape(info.error)}", soft_wrap=True)
