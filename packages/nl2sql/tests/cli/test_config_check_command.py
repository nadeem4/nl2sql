"""``nl2sql config check [--connect]``: the whole config, checked without starting the engine.

It loads every file, resolves includes and ``${env:...}``, and loads and
checks every component the config names through the registry. ``--connect``
also connects to each datasource. Problems are printed by file; a secret is
reported as present or missing, never printed. Exit code 0 means no problems,
1 means problems, 2 is a usage error. Nothing is written under
``storage.path``. See docs/architecture/configuration_v2.md, sections 6 and 10.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()

SHOP = {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}
LOST = {"id": "lost", "type": "sqlite", "connection": {"database": "no/such/folder/lost.db"}}


def _out(result) -> str:
    return " ".join(result.output.split())


def _shop(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")


def _check(config: Path, *args: str):
    return runner.invoke(app, ["--config", str(config), "config", "check", *args])


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _split_project(folder: Path, *, datasource: dict, llm: dict) -> Path:
    """nl2sql.yaml that includes a datasources folder and llm.yaml."""
    _write(folder / "nl2sql.yaml", {
        "version": 2,
        "datasources": {"file": "datasources/"},
        "llm": {"file": "llm.yaml"},
        "embeddings": {"provider": "local"},
        "policies": {"allow_all": True},
        "storage": {"path": ".nl2sql"},
    })
    _write(folder / "datasources" / f"{datasource['id']}.yaml",
           {k: v for k, v in datasource.items() if k != "id"})
    _write(folder / "llm.yaml", llm)
    return folder / "nl2sql.yaml"


def test_a_valid_config_exits_0(tmp_path):
    _shop(tmp_path)
    make_config(tmp_path, datasources=[SHOP])

    result = _check(tmp_path / "nl2sql.yaml")

    assert result.exit_code == 0, result.output
    assert "no problems" in _out(result)


def test_a_missing_config_exits_1(tmp_path):
    result = _check(tmp_path / "nl2sql.yaml")

    assert result.exit_code == 1, result.output
    assert "not found" in _out(result)


def test_a_usage_error_exits_2(tmp_path):
    make_config(tmp_path)

    result = _check(tmp_path / "nl2sql.yaml", "--no-such-flag")

    assert result.exit_code == 2, result.output


def test_component_problems_are_grouped_by_the_file_they_are_in(tmp_path):
    _shop(tmp_path)
    config = _split_project(tmp_path, datasource={**SHOP, "type": "sqlit"},
                            llm={"provider": "antropic", "model": "claude-test"})

    result = _check(config)

    assert result.exit_code == 1, result.output
    out = _out(result)
    assert "2 problems" in out
    sales, llm = out.index("datasources/shop.yaml"), out.index("llm.yaml")
    assert out.index("Did you mean 'sqlite'?") > sales
    assert out.index("Did you mean 'anthropic'?") > llm
    # One heading per file: the problem sits under its own file's heading.
    assert (out.index("Did you mean 'sqlite'?") < llm) == (sales < llm)


def test_load_problems_are_grouped_by_file(tmp_path):
    _shop(tmp_path)
    config = _split_project(tmp_path, datasource={**SHOP, "preferd_tables": ["customers"]},
                            llm={"provider": "openai", "model": "gpt-test", "temprature": 0})

    result = _check(config)

    assert result.exit_code == 1, result.output
    out = _out(result)
    assert "datasources/shop.yaml" in out and "Did you mean 'preferred_tables'?" in out
    assert "llm.yaml" in out and "Did you mean 'temperature'?" in out


def test_secrets_are_reported_present_or_missing_never_printed(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_OPENAI_KEY", "sk-team-value-never-printed")
    monkeypatch.delenv("TEAM_PLANNER_KEY", raising=False)
    _shop(tmp_path)
    make_config(tmp_path, datasources=[SHOP], llm={
        "provider": "openai", "model": "gpt-test", "api_key": "${env:TEAM_OPENAI_KEY}",
        "agents": {"ast_planner": {"api_key": "${env:TEAM_PLANNER_KEY}"}},
    })

    result = _check(tmp_path / "nl2sql.yaml")

    out = _out(result)
    assert "sk-team-value-never-printed" not in result.output
    assert "${env:TEAM_OPENAI_KEY} present" in out
    assert "${env:TEAM_PLANNER_KEY} missing" in out


def test_without_connect_a_datasource_that_cannot_connect_is_not_a_problem(tmp_path):
    _shop(tmp_path)
    make_config(tmp_path, datasources=[SHOP, LOST])

    result = _check(tmp_path / "nl2sql.yaml")

    assert result.exit_code == 0, result.output


def test_connect_reports_a_failed_datasource(tmp_path):
    _shop(tmp_path)
    make_config(tmp_path, datasources=[SHOP, LOST])

    result = _check(tmp_path / "nl2sql.yaml", "--connect")

    assert result.exit_code == 1, result.output
    out = _out(result)
    assert "datasources[1].connection" in out
    assert "lost" in out and "shop: ready" in out


def test_no_files_are_created_under_storage_path(tmp_path):
    _shop(tmp_path)
    make_config(tmp_path, datasources=[SHOP], storage={"path": "store"})

    for args in ([], ["--connect"]):
        result = _check(tmp_path / "nl2sql.yaml", *args)
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "store").exists()
