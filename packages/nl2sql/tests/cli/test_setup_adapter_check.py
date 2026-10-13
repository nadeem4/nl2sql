"""The wizard's adapter check, for a bring-your-own datasource.

`nl2sql setup` reads `configs/datasources.yaml` and offers the driver extra
each configured adapter needs. The keys it matches on must be the adapter
types a datasource is configured with, which are the `nl2sql.adapters`
entry-point names.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

from nl2sql.cli import config as cli_config
from nl2sql.cli.commands import setup as wizard
from nl2sql.configs import ConfigManager

PYPROJECT = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_known_adapters_are_the_entry_point_names():
    entry_points = _pyproject()["project"]["entry-points"]["nl2sql.adapters"]
    assert set(cli_config.KNOWN_ADAPTERS) == set(entry_points)
    assert set(cli_config.ADAPTER_DRIVERS) == set(entry_points)


def test_every_install_hint_names_a_real_extra():
    extras = set(_pyproject()["project"]["optional-dependencies"])
    for name, pkg in cli_config.KNOWN_ADAPTERS.items():
        if "[" in pkg:
            extra = pkg.split("[", 1)[1].rstrip("]")
            assert extra in extras, f"{name}: {pkg} names no extra in pyproject"


class _Confirm:
    def __init__(self, answers, message):
        answers.append(message)

    def execute(self):
        return False


class _Inquirer:
    def __init__(self):
        self.asked = []

    def confirm(self, message, default=True):
        return _Confirm(self.asked, message)


@pytest.fixture()
def adapter_check(tmp_path, monkeypatch):
    """Runs the adapter check over one datasource of the given type."""
    printed = []
    monkeypatch.setattr(wizard.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(wizard, "print_step", lambda *a, **k: None)
    fake = _Inquirer()
    monkeypatch.setattr(wizard, "inquirer", fake)
    checked = []
    monkeypatch.setattr(wizard, "check_package", lambda mod: checked.append(mod) or False)

    def run(conn_yaml: str):
        path = tmp_path / "configs" / "datasources.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "version: 1\ndatasources:\n  - id: main_db\n    connection:\n" + conn_yaml,
            encoding="utf-8",
        )
        monkeypatch.setattr(wizard, "DATASOURCE_CONFIG", path)
        wizard._install_required_adapters(ConfigManager(tmp_path, ds_config_path=path))
        return printed, fake.asked, checked

    return run


SERVER = "      host: localhost\n      port: 1\n      user: u\n      password: p\n      database: d\n"


@pytest.mark.parametrize(
    "conn_type, conn_yaml, driver, pkg",
    [
        ("postgres", SERVER, "psycopg2", "nl2sql-engine[postgres]"),
        ("mysql", SERVER, "pymysql", "nl2sql-engine[mysql]"),
        ("mssql", SERVER, "pyodbc", "nl2sql-engine[mssql]"),
        ("duckdb", "      database: x.duckdb\n", "duckdb_engine", "nl2sql-engine[duckdb]"),
    ],
)
def test_missing_driver_is_offered_with_the_right_extra(adapter_check, conn_type, conn_yaml, driver, pkg):
    printed, asked, checked = adapter_check(f"      type: {conn_type}\n" + conn_yaml)

    assert not any("Failed to check adapters" in line for line in printed), printed
    assert checked == [driver]
    assert asked == [f"Required adapter {pkg} is missing. Install now?"]


def test_a_type_that_only_contains_an_adapter_name_is_not_matched(adapter_check):
    printed, asked, checked = adapter_check("      type: postgres_custom\n" + SERVER)

    assert not any("Failed to check adapters" in line for line in printed), printed
    assert checked == []
    assert asked == []


class _Answer:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def test_wizard_offers_duckdb_as_a_datasource_type(tmp_path, monkeypatch):
    offered = []

    class _Wizard:
        def select(self, message, choices, default=None):
            offered.extend(choices)
            return _Answer("duckdb")

        def text(self, message, default=None, **kwargs):
            return _Answer("./warehouse.duckdb")

    path = tmp_path / "configs" / "datasources.yaml"
    monkeypatch.setattr(wizard, "DATASOURCE_CONFIG", path)
    monkeypatch.setattr(wizard, "inquirer", _Wizard())
    monkeypatch.setattr(wizard.console, "print", lambda *a, **k: None)

    wizard._configure_datasource(ConfigManager(tmp_path, ds_config_path=path))

    assert "duckdb" in offered
    written = ConfigManager(tmp_path, ds_config_path=path).load_datasources()
    assert written[0].connection.type == "duckdb"
    assert written[0].connection.model_dump()["database"] == "./warehouse.duckdb"
