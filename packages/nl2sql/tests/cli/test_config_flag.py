"""The CLI reads one config: the global ``--config`` (configuration v2, section 10).

``nl2sql --config prod/nl2sql.yaml <command>``. Without it, ``NL2SQL_CONFIG``,
then ``./nl2sql.yaml``. The ``.env`` next to that file is loaded without
overriding a variable already set. The per-command path flags, ``--env``,
``--env-file`` and ``nl2sql setup`` are gone.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()

REMOVED_FLAGS = {"--env", "--env-file", "--llm-config", "--secrets-config", "--policies-config",
                 "--vector-store", "--curated", "--secrets", "--policies"}

# Every command that reads the config, with the arguments it needs to get that far.
COMMANDS = [
    ["run", "How many customers are there?"],
    ["index"],
    ["trace", "replay", "some-trace-id"],
    ["trace", "show", "some-trace-id"],
    ["policy", "validate"],
    ["curated", "validate"],
    ["curated", "test"],
    ["curated", "proposals", "export"],
    ["cache", "clear"],
    ["feedback", "list"],
    ["feedback", "stats"],
    ["benchmark", "--tier", "1"],
    ["benchmark", "retrieval"],
]


def _commands(group: click.Group, prefix=()):
    for name, command in group.commands.items():
        yield prefix + (name,), command
        if isinstance(command, click.Group):
            yield from _commands(command, prefix + (name,))


def _shop(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    return folder


def _project(tmp_path: Path, **sections) -> Path:
    _shop(tmp_path)
    make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                **sections)
    return tmp_path / "nl2sql.yaml"


def test_the_cli_has_one_global_config_flag():
    root = typer.main.get_command(app)

    assert "--config" in {opt for param in root.params for opt in param.opts}
    for path, command in _commands(root):
        flags = {opt for param in command.params for opt in param.opts}
        assert not flags & (REMOVED_FLAGS | {"--config"}), f"nl2sql {' '.join(path)} still takes {flags & (REMOVED_FLAGS | {'--config'})}"


def test_setup_is_gone():
    result = runner.invoke(app, ["setup"])

    assert result.exit_code != 0
    assert "No such command" in result.output


@pytest.mark.parametrize("args", COMMANDS, ids=lambda a: " ".join(a))
def test_every_command_reads_the_config_flag(tmp_path, args):
    missing = tmp_path / "elsewhere" / "nl2sql.yaml"

    result = runner.invoke(app, ["--config", str(missing), *args])

    assert result.exit_code == 1, result.output
    assert f"{missing.as_posix()} not found" in result.output.replace("\n", "")


def test_nl2sql_config_names_the_file_without_the_flag(tmp_path, monkeypatch):
    missing = tmp_path / "from-variable" / "nl2sql.yaml"
    monkeypatch.setenv("NL2SQL_CONFIG", str(missing))

    result = runner.invoke(app, ["policy", "validate"])

    assert result.exit_code == 1
    assert f"{missing.as_posix()} not found" in result.output.replace("\n", "")


def test_the_env_file_next_to_the_config_is_loaded_without_overriding(tmp_path, monkeypatch):
    config = _project(tmp_path)
    (tmp_path / ".env").write_text("NL2SQL_TEST_FROM_FILE=file\nNL2SQL_TEST_SET=file\n", encoding="utf-8")
    monkeypatch.delenv("NL2SQL_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("NL2SQL_TEST_SET", "shell")

    result = runner.invoke(app, ["--config", str(config), "cache", "clear"])

    assert result.exit_code == 0, result.output
    assert os.environ["NL2SQL_TEST_FROM_FILE"] == "file"
    assert os.environ["NL2SQL_TEST_SET"] == "shell"


def test_policy_validate_checks_the_configs_policies(tmp_path):
    config = _project(tmp_path, policies={"roles": {
        "analyst": {"description": "Shop only", "allow": ["shop"]},
        "ghost": {"description": "A datasource that does not exist", "allow": ["nowhere"]},
    }, "default_role": "analyst"})

    result = runner.invoke(app, ["--config", str(config), "policy", "validate"])

    assert result.exit_code == 1, result.output
    assert "analyst" in result.output and "MISSING" in result.output


def test_cache_clear_reads_the_state_store_under_storage_path(tmp_path):
    config = _project(tmp_path)

    result = runner.invoke(app, ["--config", str(config), "cache", "clear"])

    assert result.exit_code == 0, result.output
    assert "Cleared 0 cached plans" in result.output.replace("\n", "")
    assert (tmp_path / ".nl2sql" / "state.db").exists()


@pytest.mark.parametrize("args, seam", [
    (["run", "How many customers are there?"], "nl2sql.cli.main.run_pipeline"),
    (["index"], "nl2sql.cli.main.run_indexing"),
])
def test_run_and_index_build_the_engine_from_the_config_and_check_it(tmp_path, monkeypatch, args, seam):
    config = _project(tmp_path)
    engines = []
    monkeypatch.setattr(seam, lambda *a, **k: engines.append(a[1] if args[0] == "run" else a[0]))

    result = runner.invoke(app, ["--config", str(config), *args])

    assert result.exit_code == 0, result.output
    [engine] = engines
    assert engine.config.folder == tmp_path.absolute()
    assert [d.id for d in engine.config.datasources] == ["shop"]
