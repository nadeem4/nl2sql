from __future__ import annotations

import pytest
from typer.testing import CliRunner

from nl2sql.common.settings import settings
from nl2sql.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Run inside an empty project so only the env file under test is visible."""
    monkeypatch.chdir(tmp_path)
    for key in ("ENV", "APP_ENV", "ENV_FILE_PATH", "DATASOURCE_CONFIG"):
        monkeypatch.delenv(key, raising=False)


def _write_env(path, datasource_config):
    path.write_text(f"DATASOURCE_CONFIG={datasource_config}\n", encoding="utf-8")


def test_env_flag_loads_matching_env_file(tmp_path):
    _write_env(tmp_path / ".env.demo", "configs/datasources.demo.yaml")

    result = runner.invoke(app, ["--env", "demo", "list-adapters"])

    assert result.exit_code == 0, result.output
    assert settings.datasource_config_path == "configs/datasources.demo.yaml"


def test_env_file_flag_loads_explicit_path(tmp_path):
    custom = tmp_path / "custom.env"
    _write_env(custom, "configs/datasources.custom.yaml")

    result = runner.invoke(app, ["--env-file", str(custom), "list-adapters"])

    assert result.exit_code == 0, result.output
    assert settings.datasource_config_path == "configs/datasources.custom.yaml"


def test_env_file_flag_wins_over_env_flag(tmp_path):
    _write_env(tmp_path / ".env.demo", "configs/datasources.demo.yaml")
    custom = tmp_path / "custom.env"
    _write_env(custom, "configs/datasources.custom.yaml")

    result = runner.invoke(
        app, ["--env", "demo", "--env-file", str(custom), "list-adapters"]
    )

    assert result.exit_code == 0, result.output
    assert settings.datasource_config_path == "configs/datasources.custom.yaml"


def test_no_env_flag_leaves_settings_untouched(tmp_path):
    before = settings.datasource_config_path

    result = runner.invoke(app, ["list-adapters"])

    assert result.exit_code == 0, result.output
    assert settings.datasource_config_path == before
