"""``nl2sql keys``: the API keys MCP over HTTP verifies (docs/architecture/wave_c.md, section 9; D127).

* ``create`` -- a key for a user and roles; the token is printed once.
* ``list``   -- the keys, never a secret.
* ``revoke`` -- revoke a key by its id.

``--as`` builds the admin ``by`` (repeatable), as ``nl2sql review --as`` does. A stub: M1 fills it.
"""
from __future__ import annotations

from typing import List, Optional

import typer
from typing_extensions import Annotated

from nl2sql.cli.common.planned import not_yet

app = typer.Typer(help="API keys for MCP over HTTP: `create`, `list`, `revoke`.", no_args_is_help=True)

AsOption = Annotated[Optional[List[str]], typer.Option(
    "--as", help="An admin role to act as (repeatable). Without one, policies.default_role.")]


@app.command("create")
def create(
    ctx: typer.Context,
    user: Annotated[str, typer.Option("--user", help="The user id the key's runs are recorded under.")],
    role: Annotated[List[str], typer.Option("--role", help="A role the key runs as (repeatable).")],
    as_: AsOption = None,
    tenant: Annotated[Optional[str], typer.Option("--tenant", help="The key's tenant.")] = None,
    label: Annotated[str, typer.Option("--label", help="A note to tell keys apart.")] = "",
) -> None:
    """Create a key. The token is printed once; only its hash is kept."""
    not_yet("nl2sql keys create", "M1")


@app.command("list")
def list_keys(ctx: typer.Context, as_: AsOption = None) -> None:
    """List the keys: id, label, user, roles, created and revoked times."""
    not_yet("nl2sql keys list", "M1")


@app.command("revoke")
def revoke(ctx: typer.Context, key_id: Annotated[str, typer.Argument(help="The key's id")],
           as_: AsOption = None) -> None:
    """Revoke a key; it stops verifying at once."""
    not_yet("nl2sql keys revoke", "M1")