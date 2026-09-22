from __future__ import annotations

import pytest
import yaml
from rich.console import Console

from nl2sql.context import NL2SQLContext
from nl2sql.cli.demo.manager import DemoManager


@pytest.fixture()
def demo_project(tmp_path, monkeypatch):
    """A throwaway project root with a complete Chinook demo already generated."""
    monkeypatch.chdir(tmp_path)
    manager = DemoManager(Console(), tmp_path)
    manager.setup_chinook(api_key="test-key")
    return manager


def test_index_demo_data_hands_a_context_to_run_indexing(demo_project, monkeypatch):
    captured = []

    def fake_run_indexing(ctx, enrich=True):
        captured.append((ctx, enrich))

    monkeypatch.setattr(
        "nl2sql.cli.common.indexing.run_indexing", fake_run_indexing
    )

    assert demo_project.index_demo_data() is True

    assert len(captured) == 1
    assert isinstance(captured[0][0], NL2SQLContext)
    # Enrichment spends tokens, so the demo only runs it when asked.
    assert captured[0][1] is False


def test_index_demo_data_context_points_at_demo_config(demo_project, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(
        "nl2sql.cli.common.indexing.run_indexing", lambda ctx, enrich=True: captured.append(ctx)
    )

    assert demo_project.index_demo_data() is True

    ctx = captured[0]
    assert sorted(a.datasource_id for a in ctx.ds_registry.list_adapters()) == ["chinook"]
    assert str(tmp_path) in str(ctx.vector_store.persist_directory)


def test_index_demo_data_reports_missing_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = DemoManager(Console(), tmp_path)

    assert manager.index_demo_data() is False


def test_setup_writes_the_secrets_envelope(demo_project, tmp_path):
    """Every generated `.env.demo` points SECRETS_CONFIG at this file."""
    secrets_path = tmp_path / "configs" / "secrets.demo.yaml"

    assert secrets_path.exists()
    assert yaml.safe_load(secrets_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "providers": [],
    }
