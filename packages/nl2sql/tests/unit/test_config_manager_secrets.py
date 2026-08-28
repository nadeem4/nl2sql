"""``ConfigManager`` loads config verbatim; it never resolves secret references.

Secrets are resolved at point of use by ``DatasourceRegistry`` and
``LLMRegistry``. These tests pin that contract so resolution is not
reintroduced into the loader by accident - a loader that resolved secrets
would put real credentials into any config written back to disk.
"""

from __future__ import annotations

import pytest

from nl2sql.configs import ConfigManager


@pytest.fixture()
def project(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "datasources.yaml").write_text(
        "version: 1\n"
        "datasources:\n"
        "  - id: main_db\n"
        "    connection:\n"
        "      type: postgres\n"
        "      host: localhost\n"
        "      database: app\n"
        "      username: app\n"
        "      password: ${env:NL2SQL_TEST_DB_PASSWORD}\n",
        encoding="utf-8",
    )
    (configs / "llm.yaml").write_text(
        "version: 1\n"
        "default:\n"
        "  provider: openai\n"
        "  model: gpt-4o-mini\n"
        "  api_key: ${env:NL2SQL_TEST_API_KEY}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_load_datasources_keeps_secret_references_unresolved(project, monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_DB_PASSWORD", "resolved-password")

    configs = ConfigManager(project).load_datasources()

    assert configs[0].connection.password == "${env:NL2SQL_TEST_DB_PASSWORD}"


def test_load_llm_keeps_secret_references_unresolved(project, monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_API_KEY", "resolved-key")

    config = ConfigManager(project).load_llm()

    assert config.default.api_key.get_secret_value() == "${env:NL2SQL_TEST_API_KEY}"
