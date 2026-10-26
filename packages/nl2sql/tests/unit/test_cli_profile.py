"""``nl2sql profile`` and ``nl2sql index --profile`` (docs/architecture/wave_b.md, section 6, P1).

The index is written without embedding (a snapshot and a pointer), as in ``test_cli_knowledge``, so no model
is loaded; ``index --profile`` runs with the indexing step replaced by that.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nl2sql import NL2SQL, load_config
from nl2sql.cli.main import app
from nl2sql.testing.config import make_config
from nl2sql_sdk.stores import BuildPointer

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "reader": {"description": "Customers only", "allow": ["shop.main.customers"]}}


def _index(engine: NL2SQL) -> None:
    ctx = engine._runtime
    adapter = ctx.ds_registry.get_adapter("shop")
    version = ctx.state.put_snapshot(adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope("shop")))
    ctx.index.open(ctx.namespace, 384)
    ctx.index.set_pointers(ctx.namespace, {"shop": BuildPointer(
        build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})


@pytest.fixture
def project(tmp_path):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.executescript("""
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, total NUMERIC);
            INSERT INTO customers VALUES (1, 'a'), (2, 'b');
            INSERT INTO orders VALUES (1, 1, 5.0), (2, 2, 7.5);
        """)
    connection.close()
    make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                policies={"roles": ROLES})
    with NL2SQL(load_config(tmp_path / "nl2sql.yaml")) as engine:
        _index(engine)
    return tmp_path


def _run(project: Path, *args: str):
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), *args])
    return result, ANSI.sub("", result.stdout)


def test_profile_prints_the_report_and_what_waits_for_review(project):
    result, out = _run(project, "profile", "--as", "admin")

    assert result.exit_code == 0, out
    assert "shop: 1 join" in out and "1 overlap probes" in out
    assert "Waiting for review" in out and "orders.customer_id->customers.customer_id" in out and "money" in out


def test_profile_needs_a_caller(project):
    result, out = _run(project, "profile")
    assert result.exit_code == 1 and "--as" in out
    result, out = _run(project, "profile", "--as", "nobody")
    assert result.exit_code == 1 and "Unknown role" in out


def test_profile_an_unknown_datasource_exits_1(project):
    result, out = _run(project, "profile", "--as", "admin", "--datasource", "nope")
    assert result.exit_code == 1 and "Unknown datasource" in out


def test_profile_as_a_narrow_role_files_only_what_it_reads(project):
    result, out = _run(project, "profile", "--as", "reader")

    assert result.exit_code == 0, out
    assert "orders" not in out


def test_index_profile_indexes_then_profiles(project, monkeypatch):
    calls = []
    monkeypatch.setattr("nl2sql.cli.main.run_indexing", lambda engine, **kwargs: calls.append(kwargs))

    result, out = _run(project, "index", "--profile", "--as", "admin", "-d", "shop")

    assert result.exit_code == 0, out
    assert calls == [{"enrich": False, "datasource_ids": ["shop"]}]
    assert "Waiting for review" in out


def test_index_profile_option_combinations_are_refused(project):
    for args in (["index", "--profile", "--check"], ["index", "--profile", "--curated-only"],
                 ["index", "--as", "admin"]):
        result, out = _run(project, *args)
        assert result.exit_code == 2, (args, out)


def test_index_profile_checks_the_caller_before_indexing(project, monkeypatch):
    calls = []
    monkeypatch.setattr("nl2sql.cli.main.run_indexing", lambda engine, **kwargs: calls.append(kwargs))

    for args in (["index", "--profile"], ["index", "--profile", "--as", "nobody"]):
        result, out = _run(project, *args)
        assert result.exit_code == 1, (args, out)

    assert calls == []
