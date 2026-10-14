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


def test_the_demo_logs_at_the_level_its_project_env_file_names(tmp_path, monkeypatch, demo_engines):
    pytest.importorskip("fastapi")
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    directory = tmp_path / "demo"
    first = runner.invoke(app, ["demo", "--no-browser", "--dir", str(directory)])
    assert first.exit_code == 0, first.output
    with (directory / ".env").open("a", encoding="utf-8") as env:
        env.write("\nNL2SQL_LOG_LEVEL=DEBUG\n")

    second = runner.invoke(app, ["demo", "--no-browser", "--dir", str(directory)])

    assert second.exit_code == 0, second.output
    assert logging.getLogger().level == logging.DEBUG
