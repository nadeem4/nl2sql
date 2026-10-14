"""``check_config``: the engine side of ``nl2sql config check``.

Steps 1, 2a and 2b of loading (docs/architecture/configuration_v2.md,
section 6) against the registry passed in, without building any store.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from nl2sql import NL2SQL, ConfigError, load_config
from nl2sql.config.check import check_config
from nl2sql.registry import Registry


def _adapter(drivers=(), hint=""):
    return type("Adapter", (), {"interface_version": 1, "driver_modules": drivers, "install_hint": hint})


def _project(folder: Path, datasource: dict) -> Path:
    (folder / "datasources").mkdir(parents=True)
    (folder / "nl2sql.yaml").write_text(yaml.safe_dump({
        "version": 2,
        "datasources": {"file": "datasources/"},
        "llm": {"provider": "openai", "model": "gpt-test"},
        "embeddings": {"provider": "local"},
        "policies": {"allow_all": True},
        "storage": {"path": ".nl2sql"},
    }), encoding="utf-8")
    (folder / "datasources" / f"{datasource['id']}.yaml").write_text(
        yaml.safe_dump({k: v for k, v in datasource.items() if k != "id"}), encoding="utf-8")
    return folder / "nl2sql.yaml"


def test_a_missing_driver_is_a_problem_with_its_install_hint(tmp_path):
    registry = Registry.default()
    registry.add_adapter("snowflake", _adapter(drivers=("nl2sql_no_such_driver",),
                                               hint='pip install "nl2sql-snowflake[driver]"'))
    config = _project(tmp_path, {"id": "sales", "type": "snowflake", "connection": {}})

    report = check_config(config, registry=registry)

    assert not report.ok
    [problem] = report.problems
    assert (problem.file, problem.path) == ("datasources/sales.yaml", "type")
    assert "nl2sql_no_such_driver" in problem.message
    assert 'pip install "nl2sql-snowflake[driver]"' in problem.message
    assert not (tmp_path / ".nl2sql").exists()


def test_the_engine_names_the_included_file_a_component_problem_is_in(tmp_path):
    config = _project(tmp_path, {"id": "sales", "type": "sqlit", "connection": {}})

    try:
        NL2SQL(load_config(config))
    except ConfigError as error:
        [problem] = error.problems
    else:
        raise AssertionError("NL2SQL accepted an unknown adapter type")

    assert (problem.file, problem.path) == ("datasources/sales.yaml", "type")
