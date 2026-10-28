"""``nl2sql keys``: the API keys MCP over HTTP verifies (docs/architecture/wave_c.md, section 9; D127).

* ``create`` -- a key for a user and roles; the token is printed once.
* ``list``   -- the keys, never a secret.
* ``revoke`` -- revoke a key by its id.

Each calls the matching ``engine.api_keys`` method as the admin ``--as`` (repeatable) builds, as ``nl2sql review``
does. A refusal exits 1.
"""
from __future__ import annotations

from typing import List, Optional

import typer
from rich.markup import escape
from typing_extensions import Annotated

from nl2sql.cli.commands.knowledge import AsOption, UserOption, caller
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import console

app = typer.Typer(help="API keys for MCP over HTTP: `create`, `list`, `revoke`.", no_args_is_help=True)


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}", soft_wrap=True)
    raise typer.Exit(1)


def _engine_and_admin(ctx: typer.Context, roles: Optional[List[str]]):
    # Nothing here connects to a datasource, so the engine is not checked.
    engine = build_engine(ctx, check=False)
    try:
        return engine, caller(engine, list(roles or []), None)
    except ValueError as error:
        engine.close()
        _fail(str(error))


def _run(call):
    """``call()``, a refusal printed and exit 1."""
    try:
        return call()
    except (PermissionError, ValueError) as error:
        _fail(str(error))


@app.command("create")
def create(
    ctx: typer.Context,
    user: Annotated[str, typer.Option("--user", help="The user id the key's runs are recorded under.")],
    role: Annotated[Optional[List[str]], typer.Option(
        "--role", help="A role the key runs as (repeatable); none under allow_all, where no roles exist.")] = None,
    as_: AsOption = None,
    tenant: Annotated[Optional[str], typer.Option("--tenant", help="The key's tenant.")] = None,
    label: Annotated[str, typer.Option("--label", help="A note to tell keys apart.")] = "",
) -> None:
    """Create a key. The token is printed once; only its hash is kept."""
    engine, admin = _engine_and_admin(ctx, as_)
    with engine:
        key, token = _run(lambda: engine.api_keys.create(user_id=user, roles=list(role or []), by=admin, tenant_id=tenant,
                                                         label=label))
    console.print(f"[green]created[/green] key {escape(key.id)} for {escape(key.user_id)} "
                  f"({escape(', '.join(key.roles))})", soft_wrap=True)
    console.print(f"Token (shown once, keep it safe): {escape(token)}", soft_wrap=True, highlight=False)


@app.command("list")
def list_keys(ctx: typer.Context, as_: AsOption = None) -> None:
    """List the keys: id, label, user, roles, created and revoked times."""
    engine, admin = _engine_and_admin(ctx, as_)
    with engine:
        listed = _run(lambda: engine.api_keys.list(by=admin))
    if not listed:
        console.print("No API keys.", soft_wrap=True)
    for key in listed:
        state = f"[red]revoked {key.revoked_at.isoformat(timespec='seconds')}[/red]" if key.revoked_at else "active"
        tenant = f" tenant {escape(key.tenant_id)}" if key.tenant_id else ""
        label = f' "{escape(key.label)}"' if key.label else ""
        console.print(f"{escape(key.id)}  {escape(key.user_id)}{tenant}  {escape('[' + ', '.join(key.roles) + ']')}{label}  "
                      f"created {key.created_at.isoformat(timespec='seconds')}  {state}", soft_wrap=True)


@app.command("revoke")
def revoke(ctx: typer.Context, key_id: Annotated[str, typer.Argument(help="The key's id")],
           as_: AsOption = None) -> None:
    """Revoke a key; it stops verifying at once."""
    engine, admin = _engine_and_admin(ctx, as_)
    with engine:
        _run(lambda: engine.api_keys.revoke(key_id, by=admin))
    console.print(f"[yellow]revoked[/yellow] key {escape(key_id)}", soft_wrap=True)
