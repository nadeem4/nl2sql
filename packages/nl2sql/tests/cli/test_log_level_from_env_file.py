"""``NL2SQL_LOG_LEVEL`` set only in the ``.env`` next to ``nl2sql.yaml`` takes effect.

Every entry point loads that ``.env`` before it configures logging; a variable
already exported still wins. ``nl2sql-api`` is covered in
``packages/api/tests/test_startup.py``.
"""
import logging

import pytest
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    # delenv on an absent variable records nothing to restore, so set it
    # first: whatever the .env adds is then removed afterwards.
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "")
    monkeypatch.delenv("NL2SQL_LOG_LEVEL")
    monkeypatch.chdir(tmp_path)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


def _project(folder, level="DEBUG"):
    make_config(folder)
    (folder / ".env").write_text(f"NL2SQL_LOG_LEVEL={level}\n", encoding="utf-8")
    return folder / "nl2sql.yaml"


def test_the_cli_logs_at_the_level_its_env_file_names(tmp_path):
    config = _project(tmp_path / "project")

    runner.invoke(app, ["--config", str(config), "config", "check"])

    assert logging.getLogger().level == logging.DEBUG


def test_an_exported_level_wins_over_the_env_file(tmp_path, monkeypatch):
    config = _project(tmp_path / "project")
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "ERROR")

    runner.invoke(app, ["--config", str(config), "config", "check"])

    assert logging.getLogger().level == logging.ERROR
