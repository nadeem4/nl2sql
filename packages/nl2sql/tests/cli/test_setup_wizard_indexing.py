from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.cli.commands import setup as wizard


@pytest.fixture()
def wizard_project(tmp_path, monkeypatch):
    """A project root shaped the way the wizard leaves it after configuration."""
    monkeypatch.chdir(tmp_path)

    # Building a context builds the OpenAI embedder, which refuses to construct
    # without credentials. `settings` is a singleton built at import, so patch
    # the attribute rather than the environment variable. Nothing here reaches
    # the network.
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    db_path = tmp_path / "wizard.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE machines (id INTEGER PRIMARY KEY, name TEXT)")

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "datasources.yaml").write_text(
        "version: 1\n"
        "datasources:\n"
        "  - id: main_db\n"
        "    connection:\n"
        "      type: sqlite\n"
        f"      database: {db_path.as_posix()}\n",
        encoding="utf-8",
    )
    (configs / "llm.yaml").write_text(
        "version: 1\n"
        "default:\n"
        "  provider: openrouter\n"
        "  model: anthropic/claude-sonnet-4.5\n"
        "  temperature: 0.0\n"
        "  api_key: sk-or-test\n"
        "  name: default\n",
        encoding="utf-8",
    )
    (configs / "policies.json").write_text(
        json.dumps(
            {
                "version": 1,
                "roles": {
                    "admin": {
                        "role": "admin",
                        "description": "System Administrator",
                        "allowed_datasources": ["*"],
                        "allowed_tables": ["*"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(wizard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "vector_store_path", "data/vector_store_dev")
    monkeypatch.setattr(settings, "datasource_config_path", "configs/datasources.yaml")
    monkeypatch.setattr(settings, "llm_config_path", "configs/llm.yaml")
    monkeypatch.setattr(settings, "policies_config_path", "configs/policies.json")
    monkeypatch.setattr(settings, "secrets_config_path", "configs/secrets.yaml")
    return tmp_path


def test_wizard_indexing_hands_a_context_to_run_indexing(wizard_project, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "nl2sql.cli.commands.indexing.run_indexing", captured.append
    )
    monkeypatch.setattr(wizard.inquirer, "confirm", _always_yes)

    wizard._run_indexing_step()

    assert len(captured) == 1, "the wizard never reached run_indexing"
    ctx = captured[0]
    assert isinstance(ctx, NL2SQLContext)
    assert [a.datasource_id for a in ctx.ds_registry.list_adapters()] == ["main_db"]
    assert ctx.llm_registry.get_llm("default") is not None
    assert str(wizard_project) in str(pathlib.Path(ctx.vector_store.persist_directory))


def test_wizard_skips_indexing_when_declined(wizard_project, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "nl2sql.cli.commands.indexing.run_indexing", captured.append
    )
    monkeypatch.setattr(wizard.inquirer, "confirm", _always_no)

    wizard._run_indexing_step()

    assert captured == []


class _Answer:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


def _always_yes(*args, **kwargs):
    return _Answer(True)


def _always_no(*args, **kwargs):
    return _Answer(False)
