"""`nl2sql chat`: the terminal app over the engine facade (wave_d.md, section 7).

The caller is named here once: ``--user`` (default the operating-system user) and ``--as`` (a role). Every engine
call the app makes carries that ``UserContext``, so the policy a role sets holds in the terminal as it does in the API.
``textual`` is imported only when this command runs, so the other commands start without it.
"""
from __future__ import annotations

import getpass
import pathlib
from typing import Optional

import typer

from nl2sql_cli.common.decorators import handle_cli_errors
from nl2sql_cli.common.engine import build_engine


@handle_cli_errors
def chat_command(ctx: typer.Context, demo: bool, directory: pathlib.Path, role: Optional[str],
                 user: Optional[str], datasource_id: Optional[str]) -> None:
    from nl2sql import NL2SQL, UserContext, load_config, load_env_file, prepare_demo

    if demo:
        config_path = prepare_demo(directory)
        load_env_file(config_path)
        engine = NL2SQL(load_config(config_path))
    else:
        engine = build_engine(ctx)
    settings = engine.config
    roles = [role] if role else ([settings.policies.default_role] if settings.policies.default_role else [])
    caller = UserContext(user_id=user or getpass.getuser(), tenant_id=settings.settings.tenant_id, roles=roles)

    from nl2sql_cli.chat.app import ChatApp

    try:
        ChatApp(engine, caller, datasource_id=datasource_id).run()
    finally:
        engine.close()
