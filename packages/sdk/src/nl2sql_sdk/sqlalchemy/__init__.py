"""The SQLAlchemy base an adapter builds on: ``pip install "nl2sql-sdk[sqlalchemy]"``.

``SQLAlchemyAdapter`` connects, executes and reads a schema for any database
SQLAlchemy reaches; ``SQLAlchemyConnection`` adds the connection pool. The
five built-in adapters are built on it. Unlike the rest of the SDK, this
subpackage imports SQLAlchemy.
"""

from .adapter import Pool, ReflectedTable, SQLAlchemyAdapter, SQLAlchemyConnection, driver_message
from .profiling import ALL_VALUES_MAX_DISTINCT, SAMPLE_VALUES, ColumnProfile, as_scalar, column_profile

__all__ = [
    "SQLAlchemyAdapter",
    "SQLAlchemyConnection",
    "Pool",
    "ReflectedTable",
    "ColumnProfile",
    "column_profile",
    "as_scalar",
    "driver_message",
    "ALL_VALUES_MAX_DISTINCT",
    "SAMPLE_VALUES",
]
