from __future__ import annotations

import pathlib
from typing import Any, Dict, TypedDict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .datasource_config import DatasourceProfile


class UnsupportedEngineError(ValueError):
    pass


def make_engine(profile: DatasourceProfile) -> Engine:
    """
    Create a SQLAlchemy engine based on the datasource profile.
    Starts with SQLite; other engines can be added by branching on profile.engine.
    """
    engine = profile.engine.lower()
    if engine == "sqlite":
        url = profile.sqlalchemy_url
        # Ensure DB file directory exists for file-based URLs
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            if db_path and not pathlib.Path(db_path).parent.exists():
                pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, future=True)

    if engine in {"postgres", "postgresql"}:
        # Apply statement timeout and read-only session defaults via connect args.
        timeout_ms = max(profile.statement_timeout_ms, 0)
        opts = f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on"
        return create_engine(
            profile.sqlalchemy_url,
            future=True,
            connect_args={"options": opts},
            execution_options={},
        )

    if engine in {"mysql", "mariadb"}:
        return create_engine(profile.sqlalchemy_url, future=True)

    if engine in {"sqlserver", "sql_server", "mssql", "azure_sql"}:
        return create_engine(profile.sqlalchemy_url, future=True)

    raise UnsupportedEngineError(f"Unsupported engine: {profile.engine}")


def _normalize_sql(sql: str) -> str:
    # Strip whitespace and trailing semicolons to avoid sqlite multi-statement errors.
    return sql.strip().rstrip(";")


def run_read_query(
    engine: Engine, sql: str, params: Dict[str, Any] | None = None, row_limit: int = 1000
):
    """
    Execute a read-only query with a row limit safeguard. Removes trailing semicolons before applying LIMIT.
    """
    params = params or {}
    cleaned = _normalize_sql(sql)
    limited_sql = cleaned
    if " limit " not in cleaned.lower():
        limited_sql = f"{cleaned}\nLIMIT {row_limit}"
    with engine.connect() as conn:
        result = conn.execute(text(limited_sql), params)
        rows = result.fetchall()
        return rows
