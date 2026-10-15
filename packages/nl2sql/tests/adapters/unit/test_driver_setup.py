"""What a built-in adapter needs on the machine before it can connect.

MySQL 8's default login (``caching_sha2_password``) over a connection without
TLS needs PyMySQL's ``cryptography`` dependency. SQL Server needs one of
Microsoft's ODBC drivers; when the connection names none, the newest one
installed is used.
"""
from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import exc as sa_exc

from nl2sql.adapters.mssql.adapter import MssqlAdapter, MssqlConnection
from nl2sql.adapters.mysql.adapter import MysqlAdapter, MysqlConnection
from nl2sql_sdk import Limits

CRYPTOGRAPHY_MESSAGE = "'cryptography' package is required for sha256_password or caching_sha2_password auth methods"


def _mysql():
    return MysqlAdapter("my", MysqlConnection(host="h", database="d", user="u", password="p"), Limits())


def _mssql(**connection):
    return MssqlAdapter("ms", MssqlConnection(host="h", database="d", user="u", password="p", **connection),
                        Limits())


@pytest.fixture
def odbc_drivers(monkeypatch):
    """Stands in for pyodbc, listing the ODBC drivers the test gives it."""
    def install(*names):
        monkeypatch.setitem(sys.modules, "pyodbc", types.SimpleNamespace(drivers=lambda: list(names)))
    return install


# --- MySQL -------------------------------------------------------------------

def test_mysql_declares_cryptography_so_a_missing_one_is_reported_before_connecting():
    assert "cryptography" in MysqlAdapter.driver_modules
    assert MysqlAdapter.install_hint == 'pip install "nl2sql-engine[mysql]"'


@pytest.mark.parametrize("error", [
    RuntimeError(CRYPTOGRAPHY_MESSAGE),
    sa_exc.OperationalError("SELECT 1", {}, RuntimeError(CRYPTOGRAPHY_MESSAGE)),
], ids=["raw", "wrapped"])
def test_mysql_missing_cryptography_is_the_driver_stage(error):
    assert _mysql().classify(error) == "driver"


def test_mysql_missing_cryptography_fails_the_check_with_the_install_hint(monkeypatch):
    adapter = _mysql()

    def no_cryptography(**_):
        raise RuntimeError(CRYPTOGRAPHY_MESSAGE)

    monkeypatch.setattr(adapter, "build_engine", no_cryptography)
    check = adapter.test_connection()

    assert check.ok is False
    assert check.stage == "driver"
    assert check.fix == 'pip install "nl2sql-engine[mysql]"'


# --- SQL Server --------------------------------------------------------------

def test_mssql_picks_the_newest_installed_sql_server_driver(odbc_drivers):
    odbc_drivers("SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server",
                 "PostgreSQL Unicode")

    url = _mssql().url(MssqlConnection(host="h", database="d", user="u", password="p"))

    assert url.query["driver"] == "ODBC Driver 18 for SQL Server"


def test_mssql_picks_driver_17_when_it_is_the_only_one(odbc_drivers):
    odbc_drivers("SQL Server", "ODBC Driver 17 for SQL Server")

    url = _mssql().url(MssqlConnection(host="h", database="d", user="u", password="p"))

    assert url.query["driver"] == "ODBC Driver 17 for SQL Server"


def test_mssql_uses_the_driver_the_connection_names(odbc_drivers):
    odbc_drivers("ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server")

    url = _mssql().url(MssqlConnection(host="h", database="d", driver="ODBC Driver 17 for SQL Server"))

    assert url.query["driver"] == "ODBC Driver 17 for SQL Server"


@pytest.mark.parametrize("driver", ["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"])
@pytest.mark.parametrize("trust, expected", [(False, None), (True, "yes")])
def test_mssql_encrypts_with_either_driver_and_trusts_the_certificate_only_when_asked(driver, trust, expected):
    # Driver 17 does not encrypt unless told to and Driver 18 does, so the
    # adapter asks for it: the connection behaves the same whichever is picked,
    # and trust_server_certificate means the same with both.
    url = _mssql().url(MssqlConnection(host="h", database="d", driver=driver, trust_server_certificate=trust))

    assert url.query["Encrypt"] == "yes"
    assert url.query.get("TrustServerCertificate") == expected


@pytest.mark.parametrize("encrypt, expected", [(True, "yes"), (False, "no")])
def test_mssql_encrypts_unless_the_connection_turns_it_off(encrypt, expected):
    # Encrypt=no is sent, not left out: Driver 18 would otherwise encrypt anyway.
    url = _mssql().url(MssqlConnection(host="h", database="d", driver="ODBC Driver 18 for SQL Server",
                                       encrypt=encrypt))

    assert url.query["Encrypt"] == expected


def test_mssql_connection_encrypts_by_default_and_types_the_option():
    assert MssqlConnection(host="h", database="d").encrypt is True
    with pytest.raises(ValueError):
        MssqlConnection(host="h", database="d", encrypt="sometimes")
    assert MssqlConnection.model_json_schema()["properties"]["encrypt"]["type"] == "boolean"


def test_mssql_with_no_sql_server_driver_installed_is_the_driver_stage(odbc_drivers):
    odbc_drivers("SQL Server", "PostgreSQL Unicode")
    adapter = _mssql()

    check = adapter.test_connection()

    assert check.ok is False
    assert check.stage == "driver"
    assert "ODBC Driver for SQL Server" in check.message
    assert check.fix == adapter.install_hint
    assert "learn.microsoft.com" in adapter.install_hint
