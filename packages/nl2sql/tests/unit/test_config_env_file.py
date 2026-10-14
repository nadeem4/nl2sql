"""``load_env_file`` and the ``make_config`` test helper."""
from __future__ import annotations

import os

import pytest

from nl2sql.config import Config, ConfigError, load_config, load_env_file
from nl2sql.testing.config import make_config


def test_env_file_next_to_the_config_is_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("NL2SQL_TEST_ONLY_VAR", raising=False)
    (tmp_path / ".env").write_text("NL2SQL_TEST_ONLY_VAR=from-file\n", encoding="utf-8")

    loaded = load_env_file(tmp_path / "nl2sql.yaml")

    assert loaded == tmp_path / ".env"
    assert os.environ["NL2SQL_TEST_ONLY_VAR"] == "from-file"
    monkeypatch.delenv("NL2SQL_TEST_ONLY_VAR")


def test_env_file_never_overrides_a_set_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_ONLY_VAR", "already-set")
    (tmp_path / ".env").write_text("NL2SQL_TEST_ONLY_VAR=from-file\n", encoding="utf-8")

    load_env_file(tmp_path / "nl2sql.yaml")

    assert os.environ["NL2SQL_TEST_ONLY_VAR"] == "already-set"


def test_no_env_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nl2sql.yaml") is None


def test_no_path_uses_nl2sql_config(tmp_path, monkeypatch):
    monkeypatch.delenv("NL2SQL_TEST_ONLY_VAR", raising=False)
    (tmp_path / ".env").write_text("NL2SQL_TEST_ONLY_VAR=from-file\n", encoding="utf-8")
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))

    assert load_env_file() == tmp_path / ".env"
    monkeypatch.delenv("NL2SQL_TEST_ONLY_VAR")


def test_load_config_never_reads_the_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NL2SQL_TEST_DATA_DIR", raising=False)
    (tmp_path / ".env").write_text("NL2SQL_TEST_DATA_DIR=from-file\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="NL2SQL_TEST_DATA_DIR"):
        make_config(tmp_path, storage={"path": "${env:NL2SQL_TEST_DATA_DIR}"})
    assert "NL2SQL_TEST_DATA_DIR" not in os.environ


# ---------------------------------------------------------------- make_config

def test_make_config_writes_a_folder_that_loads(tmp_path):
    config = make_config(tmp_path)
    assert isinstance(config, Config)
    assert (tmp_path / "nl2sql.yaml").is_file()
    assert load_config(tmp_path / "nl2sql.yaml") == config
    assert config.storage.path == tmp_path / ".nl2sql"
    assert config.policies.allow_all is True


def test_make_config_takes_sections(tmp_path):
    config = make_config(
        tmp_path,
        datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
        settings={"tenant_id": "acme"},
    )
    assert [d.id for d in config.datasources] == ["shop"]
    assert config.settings.tenant_id == "acme"
