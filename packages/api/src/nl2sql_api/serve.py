"""``nl2sql-api serve``: run the API on one Uvicorn worker (docs/architecture/wave_d.md, D205, D206, D229).

One worker, because jobs and the state they share live in this process. With ``api.auth: none`` the server binds a
loopback host only (:func:`nl2sql_api.identity.check_bind`).

``--demo`` prepares the demo project (``nl2sql.prepare_demo``) and serves it; ``--demo --hosted`` is the public server
(:mod:`nl2sql_api.hosted`). This is the one place ``NL2SQL_MODE`` is read: the mode is resolved here (flag, then
variable, then the file's ``mode:``) and travels to the engine in its ``Config``.
"""
import argparse
import os
from typing import Optional, Sequence

import uvicorn

from nl2sql import (ConfigError, load_config, load_env_file, prepare_demo, provider_keys_in_environment,
                    resolve_mode)
from nl2sql_api import identity
from nl2sql_api.hosted import DEFAULT_QUESTIONS_PER_MINUTE, DEFAULT_QUESTIONS_PER_SESSION, Hosted, Limits
from nl2sql_api.main import create_app


def _visitor_role(config) -> str:
    """The one role every hosted visitor holds: ``policies.default_role``, else ``viewer``. Never an admin role."""
    policies = config.policies
    role = policies.default_role or ("viewer" if "viewer" in policies.roles else None)
    if not role:
        raise identity.StartRefused("The hosted demo needs a role for visitors: set policies.default_role in "
                                    "nl2sql.yaml to a role that is not an admin role.")
    if role in policies.admin_roles:
        raise identity.StartRefused(f"The hosted demo's visitor role is {role}, an admin role. Set "
                                    "policies.default_role to a role that is not in policies.admin_roles.")
    return role


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="nl2sql-api", description="NL2SQL API server")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve /api/v1 and the web app")
    serve.add_argument("--config", default=None, help="nl2sql.yaml (default: $NL2SQL_CONFIG, else ./nl2sql.yaml)")
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    serve.add_argument("--demo", action="store_true", help="Prepare the demo project in --dir and serve it")
    serve.add_argument("--dir", default="nl2sql-demo", help="The demo project's folder (default: nl2sql-demo)")
    serve.add_argument("--hosted", action="store_true",
                       help="The public demo: anonymous sessions, limits, replay only")
    serve.add_argument("--questions-per-minute", type=int, default=DEFAULT_QUESTIONS_PER_MINUTE,
                       help="Hosted: questions per minute per address")
    serve.add_argument("--questions-per-session", type=int, default=DEFAULT_QUESTIONS_PER_SESSION,
                       help="Hosted: questions per browser session")
    args = parser.parse_args(argv)

    env_mode = os.environ.get("NL2SQL_MODE")  # the only read of it in the code base (D229)
    flag = "hosted" if args.hosted else "demo" if args.demo else None
    try:
        config_path = args.config
        if args.demo:
            if flag != "hosted" and (env_mode or "").strip() != "hosted":
                # The key the demo's own .env holds; a hosted server holds none.
                load_env_file(os.path.join(args.dir, "nl2sql.yaml"))
            config_path = prepare_demo(args.dir, provider="replay" if (flag or env_mode) == "hosted" else None)
        config = load_config(config_path)
        try:
            mode = resolve_mode(flag, env_mode, config.mode)
        except ValueError as error:
            raise identity.StartRefused(str(error)) from None
        hosted = None
        if mode == "hosted":
            held = provider_keys_in_environment()
            if held:
                raise identity.StartRefused(
                    "The hosted demo must hold no model key, and the environment has one for "
                    f"{', '.join(sorted(held))}. Unset it: visitors bring their own.")
            hosted = Hosted(_visitor_role(config), Limits(args.questions_per_minute, args.questions_per_session))

        def adjust(loaded):
            api = loaded.api
            if hosted is not None:
                api = api.model_copy(update={"auth": "static", "static_role": hosted.role, "request_llm": True})
                policies = loaded.policies.model_copy(update={"default_role": hosted.role})
                return loaded.model_copy(update={"mode": mode, "api": api, "policies": policies})
            if args.demo:
                api = api.model_copy(update={"auth": "none", "request_llm": True})
            return loaded.model_copy(update={"mode": mode, "api": api})

        if hosted is None:
            identity.check_bind(adjust(config).api.auth, args.host)
    except (ConfigError, identity.StartRefused) as error:
        raise SystemExit(f"nl2sql-api: {error}") from None
    uvicorn.run(create_app(config_path, adjust=adjust, hosted=hosted), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
