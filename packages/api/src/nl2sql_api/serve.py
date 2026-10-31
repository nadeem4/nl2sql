"""``nl2sql-api serve``: run the API on one Uvicorn worker (docs/architecture/wave_d.md, D205 and D206).

One worker, because jobs and the state they share live in this process. With ``api.auth: none`` the server binds a
loopback host only (:func:`nl2sql_api.identity.check_bind`).
"""
import argparse
from typing import Optional, Sequence

import uvicorn

from nl2sql import ConfigError, load_config
from nl2sql_api import identity
from nl2sql_api.main import create_app


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="nl2sql-api", description="NL2SQL API server")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve /api/v1")
    serve.add_argument("--config", default=None, help="nl2sql.yaml (default: $NL2SQL_CONFIG, else ./nl2sql.yaml)")
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        identity.check_bind(config.api.auth, args.host)
    except (ConfigError, identity.StartRefused) as error:
        raise SystemExit(f"nl2sql-api: {error}") from None
    uvicorn.run(create_app(args.config), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
