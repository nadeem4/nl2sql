"""``engine.datasources.scan()``: finding a database in a URL, a folder and on loopback ports.

See docs/architecture/wave_c.md, section 7, "Finding a database". Real engine, real SQLite and DuckDB-shaped
files; the port probe is a fake socket; a plugin adapter proves the engine names no database itself.
"""
from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest

from nl2sql import NL2SQL, Registry
from nl2sql.api.types import Candidate
from nl2sql.testing.config import make_config
from nl2sql_sdk import ConnectionModel
from nl2sql_sdk.discovery import DiscoverableAdapter, Found


def _sqlite_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    connection.close()
    return path


class GhostConnection(ConnectionModel):
    host: str
    port: int = 9999
    token: str


class GhostAdapter:
    """A plugin adapter whose driver is not installed; it opens nothing, ever."""

    interface_version = 2
    title = "Ghost"
    connection_model = GhostConnection
    driver_modules = ("nl2sql_no_such_driver",)
    install_hint = 'pip install "ghost-driver"'
    file_suffixes = (".ghost",)
    default_ports = (9999,)
    url_schemes = ("ghost",)

    def __init__(self, *args, **kwargs):
        raise AssertionError("scan must never build an adapter")

    @classmethod
    def connection_for(cls, found: Found):
        if found.kind == "port":
            host, _, port = found.value.partition(":")
            return {"host": host, "port": int(port)}
        if found.kind == "url":
            return {"host": "ghost.example", "token": "t0ps3cret"}
        return None


@pytest.fixture
def registry() -> Registry:
    registry = Registry.default()
    registry.add_adapter("ghost", GhostAdapter)
    return registry


@pytest.fixture
def no_ports(monkeypatch):
    """Every probe says closed; records what it was asked."""
    asked = []

    def refuse(address, timeout=None, *args, **kwargs):
        asked.append((address, timeout))
        raise OSError("closed")

    monkeypatch.setattr(socket, "create_connection", refuse)
    return asked


@pytest.fixture
def engine(tmp_path: Path, registry, no_ports):
    with NL2SQL(make_config(tmp_path / "project"), registry=registry) as engine:
        yield engine


def test_the_five_built_ins_declare_what_scan_needs():
    registry = Registry.default()
    declared = {}
    for name in ("sqlite", "duckdb", "postgres", "mysql", "mssql"):
        cls = registry.load("adapter", name, require_installed=False)
        assert isinstance(cls, DiscoverableAdapter), name
        declared[name] = (cls.file_suffixes, cls.default_ports, cls.url_schemes)
    assert declared == {
        "sqlite": ((".sqlite", ".sqlite3", ".db"), (), ("sqlite",)),
        "duckdb": ((".duckdb",), (), ("duckdb",)),
        "postgres": ((), (5432,), ("postgresql", "postgres")),
        "mysql": ((), (3306,), ("mysql", "mariadb")),
        "mssql": ((), (1433,), ("mssql", "sqlserver")),
    }


def test_scan_finds_a_sqlite_file_by_suffix_and_header(engine, tmp_path):
    shop = _sqlite_file(tmp_path / "data" / "shop.sqlite")

    found = engine.datasources.scan(tmp_path / "data", ports=False)

    assert [(c.type, c.found.kind) for c in found] == [("sqlite", "file")]
    candidate = found[0]
    assert isinstance(candidate, Candidate)
    assert candidate.title == "SQLite" and candidate.installed is True
    assert Path(candidate.found.value) == shop
    assert Path(candidate.connection["database"]) == shop
    assert candidate.missing == []


def test_db_file_that_is_not_sqlite_is_skipped(engine, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "notes.db").write_bytes(b"this is not a database at all")
    (tmp_path / "data" / "empty.db").write_bytes(b"")

    assert engine.datasources.scan(tmp_path / "data", ports=False) == []


def test_a_duckdb_file_is_found_by_its_header(engine, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "wh.duckdb").write_bytes(b"\x00" * 8 + b"DUCK" + b"\x00" * 16)
    (tmp_path / "data" / "fake.duckdb").write_bytes(b"not duck")

    found = engine.datasources.scan(tmp_path / "data", ports=False)

    assert [(c.type, Path(c.found.value).name) for c in found] == [("duckdb", "wh.duckdb")]


def test_scan_stops_at_two_levels_and_skips_storage_path(tmp_path, registry, no_ports):
    root = tmp_path / "data"
    _sqlite_file(root / "top.sqlite")
    _sqlite_file(root / "a" / "b" / "two.sqlite")
    _sqlite_file(root / "a" / "b" / "c" / "three.sqlite")  # three levels down: not looked at
    _sqlite_file(root / ".hidden" / "secret.sqlite")
    _sqlite_file(root / "store" / "inside.sqlite")

    with NL2SQL(make_config(root, storage={"path": "store"}), registry=registry) as engine:
        names = {Path(c.found.value).name for c in engine.datasources.scan(root, ports=False)}

    assert names == {"top.sqlite", "two.sqlite"}


def test_scan_looks_at_no_more_than_fifty_files(engine, tmp_path):
    root = tmp_path / "data"
    for index in range(60):
        _sqlite_file(root / f"db{index:02d}.sqlite")

    assert len(engine.datasources.scan(root, ports=False)) == 50


def test_a_missing_folder_finds_nothing(engine, tmp_path):
    assert engine.datasources.scan(tmp_path / "nowhere", ports=False) == []


def test_url_scheme_with_driver_maps_to_the_adapter(engine, tmp_path):
    found = engine.datasources.scan(tmp_path, url="postgresql+psycopg2://app:pw@db.internal:5433/shop", ports=False)

    assert [(c.type, c.found.kind) for c in found] == [("postgres", "url")]
    assert found[0].connection == {"host": "db.internal", "port": 5433, "database": "shop", "user": "app",
                                   "password": "pw"}
    assert found[0].missing == []

    mysql = engine.datasources.scan(tmp_path, url="mariadb://u@h/db", ports=False)
    assert [(c.type, c.missing) for c in mysql] == [("mysql", ["password"])]
    assert engine.datasources.scan(tmp_path, url="oracle://u:p@h/db", ports=False) == []


def test_a_sqlite_url_fills_the_database(engine, tmp_path):
    found = engine.datasources.scan(tmp_path, url="sqlite:///shop.sqlite", ports=False)

    assert [(c.type, c.connection) for c in found] == [("sqlite", {"database": "shop.sqlite"})]


def test_url_password_never_prints_in_a_candidate(engine, tmp_path):
    secret = "hunter2-p@ss"
    found = engine.datasources.scan(tmp_path, url=f"postgres://app:{secret.replace('@', '%40')}@db/shop", ports=False)

    candidate = found[0]
    assert secret not in repr(candidate) and secret not in str(candidate)
    assert "hunter2" not in candidate.found.value and "hunter2" not in repr(candidate.found)
    assert candidate.found.value == "postgres://app:***@db/shop"


@pytest.mark.parametrize("url", [
    "postgresql://h/db?password=s3cr3t",
    "postgresql://u:p@h/db?sslpassword=s3cr3t#s3cr3t",
    "mssql+pyodbc://h/db?odbc_connect=DRIVER%3Dx%3BPWD%3Ds3cr3t",
])
def test_password_in_the_query_never_prints_in_a_candidate(engine, tmp_path, url):
    found = engine.datasources.scan(tmp_path, url=url, ports=False)

    assert found
    for candidate in found:
        assert "s3cr3t" not in repr(candidate) and "s3cr3t" not in str(candidate)
        assert "s3cr3t" not in candidate.found.value and "?" not in candidate.found.value


def test_a_url_without_a_port_is_the_same_server_as_the_open_port(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout=None: _FakeSocket())

    for host in ("127.0.0.1", "localhost"):
        found = engine.datasources.scan(tmp_path / "empty", url=f"postgresql://u:p@{host}/shop")

        assert [(c.type, c.found.kind) for c in found if c.type == "postgres"] == [("postgres", "url")], host


def test_each_port_is_probed_once_however_many_adapters_declare_it(engine, registry, tmp_path, monkeypatch):
    class TwinAdapter(GhostAdapter):
        pass

    registry.add_adapter("twin", TwinAdapter)
    asked = []

    def probe(address, timeout=None, *args, **kwargs):
        asked.append(address[1])
        raise OSError("closed")

    monkeypatch.setattr(socket, "create_connection", probe)

    engine.datasources.scan(tmp_path / "empty", ports=True)

    assert asked.count(9999) == 1


def test_a_plugin_returning_a_bad_value_is_skipped_not_fatal(tmp_path, registry, no_ports):
    class NoModel:
        interface_version = 2
        file_suffixes = ()
        default_ports = ()
        url_schemes = ("nomodel",)

        @classmethod
        def connection_for(cls, found):
            return {"host": "h"}

    class BadValue(GhostAdapter):
        url_schemes = ("badvalue",)

        @classmethod
        def connection_for(cls, found):
            return "password=s3cr3t"

    registry.add_adapter("nomodel", NoModel)
    registry.add_adapter("badvalue", BadValue)
    with NL2SQL(make_config(tmp_path / "project"), registry=registry) as engine:
        assert engine.datasources.scan(tmp_path, url="nomodel://x", ports=False) == []
        assert engine.datasources.scan(tmp_path, url="badvalue://x", ports=False) == []
        assert [c.type for c in engine.datasources.scan(tmp_path, url="ghost://x", ports=False)] == ["ghost"]


def test_port_probe_is_loopback_only(engine, tmp_path, monkeypatch):
    asked = []

    def probe(address, timeout=None, *args, **kwargs):
        asked.append((address, timeout))
        if address[1] == 5432:
            return _FakeSocket()
        raise OSError("closed")

    monkeypatch.setattr(socket, "create_connection", probe)
    _FakeSocket.closed = 0

    found = engine.datasources.scan(tmp_path / "empty", ports=True)

    assert asked and {host for (host, _), _ in asked} == {"127.0.0.1"}
    assert {port for (_, port), _ in asked} == {5432, 3306, 1433, 9999}
    assert {timeout for _, timeout in asked} == {0.2}
    assert [(c.type, c.found.kind, c.found.value) for c in found] == [("postgres", "port", "127.0.0.1:5432")]
    assert found[0].connection == {"host": "127.0.0.1", "port": 5432}
    assert found[0].missing == ["database", "user", "password"]
    assert _FakeSocket.closed == 1


def test_ports_false_probes_nothing(engine, tmp_path, no_ports):
    assert engine.datasources.scan(tmp_path / "empty", ports=False) == []
    assert no_ports == []


def test_order_is_url_then_files_then_ports_and_duplicates_once(engine, tmp_path, monkeypatch):
    _sqlite_file(tmp_path / "data" / "shop.sqlite")
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout=None: _FakeSocket())

    found = engine.datasources.scan(tmp_path / "data", url="postgresql://u:p@127.0.0.1:5432/shop")

    assert [(c.type, c.found.kind) for c in found] == [
        ("postgres", "url"), ("sqlite", "file"), ("ghost", "port"), ("mssql", "port"), ("mysql", "port")]


def test_scan_never_opens_a_session(engine, tmp_path, monkeypatch):
    import sqlalchemy

    _sqlite_file(tmp_path / "data" / "shop.sqlite")

    def forbidden(*args, **kwargs):
        raise AssertionError("scan opened a database session")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)

    found = engine.datasources.scan(tmp_path / "data", url="ghost://x", ports=False)  # GhostAdapter() would raise

    assert {c.type for c in found} == {"sqlite", "ghost"}


def test_uninstalled_adapter_candidate_has_its_hint(engine, tmp_path):
    found = engine.datasources.scan(tmp_path, url="ghost://anything", ports=False)

    assert len(found) == 1
    assert found[0].installed is False and found[0].install_hint == 'pip install "ghost-driver"'


def test_plugin_adapter_is_found_by_its_declarations(engine, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.ghost").write_text("x")
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout=None: _FakeSocket())

    from_url = engine.datasources.scan(tmp_path, url="ghost+v2://x", ports=False)
    from_port = [c for c in engine.datasources.scan(tmp_path / "data", ports=True) if c.type == "ghost"]
    from_file = engine.datasources.scan(tmp_path / "data", ports=False)  # connection_for refuses a file

    assert [c.title for c in from_url] == ["Ghost"] and from_url[0].missing == []
    assert from_url[0].connection["host"] == "ghost.example"
    assert [(c.found.value, c.connection, c.missing) for c in from_port] == [
        ("127.0.0.1:9999", {"host": "127.0.0.1", "port": 9999}, ["token"])]
    assert from_file == []


class _FakeSocket:
    closed = 0

    def close(self):
        _FakeSocket.closed += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
