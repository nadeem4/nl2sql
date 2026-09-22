"""Indexing the demo must not blank a real API key.

`index_demo_data` loaded `.env.demo` with ``override=True``. A new folder's
file holds an empty ``OPENAI_API_KEY=`` placeholder, so a key exported in the
shell was replaced by "" for the whole indexing run: enrichment could never
get a client, and the live index had no descriptions and no synonyms.
"""
from __future__ import annotations

import os

import pytest
from rich.console import Console

from nl2sql.cli.demo.manager import DemoManager

# Built at run time so no scanner mistakes a test fixture for a leaked key.
FAKE_KEY = "-".join(["sk", "proj", "indexingkeeps" + "k" * 24 + "7c1d"])
OTHER_KEY = "-".join(["sk", "proj", "fromtheenvfile" + "f" * 24 + "2e9a"])


@pytest.fixture
def seen(monkeypatch):
    """What indexing saw: the key in the environment and whether the
    enrichment agent could be built from it."""
    captured = {}

    def _run_indexing(ctx, *args, **kwargs):
        captured["key"] = os.environ.get("OPENAI_API_KEY")
        try:
            ctx.llm_registry.get_llm("indexing_enrichment")
            captured["enrichment_llm"] = True
        except ValueError:
            captured["enrichment_llm"] = False

    monkeypatch.setattr("nl2sql.cli.common.indexing.run_indexing", _run_indexing)
    return captured


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = DemoManager(Console(quiet=True), tmp_path)
    manager.setup_chinook()  # writes the empty OPENAI_API_KEY= placeholder
    assert "OPENAI_API_KEY=\n" in (tmp_path / ".env.demo").read_text(encoding="utf-8")
    return manager


def test_a_key_in_the_environment_survives_the_placeholder(tmp_path, monkeypatch, seen):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    manager = _project(tmp_path, monkeypatch)

    assert manager.index_demo_data() is True

    assert seen["key"] == FAKE_KEY
    assert seen["enrichment_llm"] is True
    assert os.environ["OPENAI_API_KEY"] == FAKE_KEY


def test_a_key_saved_in_env_demo_reaches_indexing(tmp_path, monkeypatch, seen):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = _project(tmp_path, monkeypatch)
    env = tmp_path / ".env.demo"
    env.write_text(env.read_text(encoding="utf-8").replace("OPENAI_API_KEY=\n", f"OPENAI_API_KEY={FAKE_KEY}\n"),
                   encoding="utf-8")

    assert manager.index_demo_data() is True

    assert seen["key"] == FAKE_KEY
    assert seen["enrichment_llm"] is True


def test_an_exported_key_wins_over_one_in_env_demo(tmp_path, monkeypatch, seen):
    """The same precedence `nl2sql demo` documents: environment, then the file."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    manager = _project(tmp_path, monkeypatch)
    env = tmp_path / ".env.demo"
    env.write_text(env.read_text(encoding="utf-8").replace("OPENAI_API_KEY=\n", f"OPENAI_API_KEY={OTHER_KEY}\n"),
                   encoding="utf-8")

    manager.index_demo_data()

    assert seen["key"] == FAKE_KEY


def test_other_settings_in_env_demo_still_apply(tmp_path, monkeypatch, seen):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    manager = _project(tmp_path, monkeypatch)

    manager.index_demo_data()

    assert os.environ["EMBEDDING_PROVIDER"] == "local"
