from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, TypedDict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from nl2sql.datasource_config import DatasourceProfile


class UnsupportedEngineError(ValueError):
    """Raised when the requested database engine is not supported."""
    pass


def make_engine(profile: DatasourceProfile) -> Engine:
    """
    Create a SQLAlchemy engine based on the datasource profile.

    Args:
        profile: The datasource configuration profile.

    Returns:
        A SQLAlchemy Engine instance.

    Raises:
        UnsupportedEngineError: If the engine type is not supported.
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
    """Strips whitespace and trailing semicolons."""
    return sql.strip().rstrip(";")


def run_read_query(
    engine: Engine, sql: str, params: Dict[str, Any] | None = None, row_limit: int = 1000
):
    """
    Execute a read-only query with a row limit safeguard.

    Removes trailing semicolons before applying LIMIT to avoid syntax errors.

    Args:
        engine: The SQLAlchemy engine.
        sql: The SQL query string.
        params: Optional query parameters.
        row_limit: Maximum number of rows to return.

    Returns:
        A list of result rows.
    """
    params = params or {}
    cleaned = _normalize_sql(sql)
    limited_sql = cleaned
    # Use regex to check for LIMIT word boundary to avoid false positives/negatives
    if not re.search(r"\blimit\b", cleaned, re.IGNORECASE):
        limited_sql = f"{cleaned}\nLIMIT {row_limit}"
    with engine.connect() as conn:
        result = conn.execute(text(limited_sql), params)
        rows = result.fetchall()
        return rows
