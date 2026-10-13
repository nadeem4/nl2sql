"""Credentials with URL-reserved characters survive URL construction.

A password such as ``p@ss:w/rd#%`` written raw into ``user:pass@host`` moves
the host boundary (``@``), the port boundary (``:``), the path (``/``) and the
fragment (``#``), so the driver dials the wrong host or rejects the URL. Each
adapter must escape them; parsing the URL back gives the original values.
"""

import pytest
from sqlalchemy.engine import make_url

from nl2sql.adapters.mssql.adapter import MssqlAdapter
from nl2sql.adapters.mysql.adapter import MysqlAdapter
from nl2sql.adapters.postgres.adapter import PostgresAdapter

PASSWORD = "p@ss:w/rd#%?&="
USER = "an@lyst:1/x"


@pytest.mark.parametrize(
    "adapter_cls, conn_type, port",
    [(PostgresAdapter, "postgres", 5432), (MysqlAdapter, "mysql", 3306), (MssqlAdapter, "mssql", 1433)],
    ids=["postgres", "mysql", "mssql"],
)
def test_reserved_characters_in_credentials_round_trip(adapter_cls, conn_type, port):
    # Arrange
    adapter = adapter_cls.__new__(adapter_cls)

    # Act
    uri = adapter.construct_uri(
        {"type": conn_type, "host": "db.internal", "user": USER, "password": PASSWORD, "database": "sales"}
    )
    url = make_url(uri)

    # Assert
    assert url.username == USER
    assert url.password == PASSWORD
    assert url.host == "db.internal"
    assert url.port == port
    assert url.database == "sales"


def test_options_survive_alongside_escaped_credentials():
    # Arrange
    adapter = PostgresAdapter.__new__(PostgresAdapter)

    # Act
    url = make_url(adapter.construct_uri({
        "type": "postgres", "host": "h", "user": "u", "password": PASSWORD,
        "database": "d", "options": {"sslmode": "require", "connect_timeout": 5},
    }))

    # Assert
    assert url.password == PASSWORD
    assert url.query == {"sslmode": "require", "connect_timeout": "5"}


def test_mssql_keeps_driver_and_trusted_connection_without_credentials():
    # Arrange
    adapter = MssqlAdapter.__new__(MssqlAdapter)

    # Act
    url = make_url(adapter.construct_uri({
        "type": "mssql", "host": "h", "database": "d", "trusted_connection": True,
    }))

    # Assert
    assert url.username is None and url.password is None
    assert url.query["driver"] == "ODBC Driver 17 for SQL Server"
    assert url.query["Trusted_Connection"] == "yes"
