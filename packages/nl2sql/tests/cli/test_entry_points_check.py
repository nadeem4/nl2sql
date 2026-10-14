"""Each entry point calls ``engine.check()`` at startup and enforces ``required``.

docs/architecture/configuration_v2.md, "Lifecycle": the CLI, ``nl2sql-api``
and the playground refuse to start on ``EngineNotReady``, and start degraded,
printing the report, when an optional datasource fails. ``nl2sql-api``'s half
of this is in packages/api/tests/test_startup.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()


def _project(tmp_path: Path, *, required: bool) -> Path:
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        # SQLite cannot create a file in a folder that does not exist, so this one never connects.
        {"id": "lost", "type": "sqlite", "connection": {"database": "no/such/folder/lost.db"},
         "required": required},
    ])
    return tmp_path / "nl2sql.yaml"


@pytest.mark.parametrize("args, seam", [
    (["run", "How many customers are there?"], "nl2sql.cli.main.run_pipeline"),
    (["index"], "nl2sql.cli.main.run_indexing"),
])
def test_entry_points_refuse_to_start_when_a_required_datasource_fails(tmp_path, monkeypatch, args, seam):
    config = _project(tmp_path, required=True)
    called = []
    monkeypatch.setattr(seam, lambda *a, **k: called.append(a))

    result = runner.invoke(app, ["--config", str(config), *args])

    assert result.exit_code == 1, result.output
    assert called == []
    output = result.output.replace("\n", " ")
    assert "lost (required): failed at database" in output


@pytest.mark.parametrize("args, seam", [
    (["run", "How many customers are there?"], "nl2sql.cli.main.run_pipeline"),
    (["index"], "nl2sql.cli.main.run_indexing"),
])
def test_an_optional_datasource_that_fails_leaves_the_cli_running_degraded(tmp_path, monkeypatch, args, seam):
    config = _project(tmp_path, required=False)
    called = []
    monkeypatch.setattr(seam, lambda *a, **k: called.append(a))

    result = runner.invoke(app, ["--config", str(config), *args])

    assert result.exit_code == 0, result.output
    assert len(called) == 1
    assert "lost: failed at database" in result.output.replace("\n", " ")


def test_the_demo_refuses_to_start_when_a_required_datasource_fails(tmp_path):
    from nl2sql.cli.commands.demo import start_engine

    config = _project(tmp_path, required=True)

    with pytest.raises(SystemExit) as exit_info:
        start_engine(config)

    assert exit_info.value.code == 1
