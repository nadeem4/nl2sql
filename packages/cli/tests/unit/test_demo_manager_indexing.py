from __future__ import annotations

import pytest
from rich.console import Console

from nl2sql.context import NL2SQLContext
from nl2sql_cli.demo.manager import DemoManager


@pytest.fixture()
def demo_project(tmp_path, monkeypatch):
    """A throwaway project root with a complete lite demo already generated."""
    monkeypatch.chdir(tmp_path)
    manager = DemoManager(Console(), tmp_path)
    manager.setup_lite(api_key="test-key")
    return manager


def test_index_demo_data_hands_a_context_to_run_indexing(demo_project, monkeypatch):
    captured = []

    def fake_run_indexing(ctx):
        captured.append(ctx)

    monkeypatch.setattr(
        "nl2sql_cli.commands.indexing.run_indexing", fake_run_indexing
    )

    assert demo_project.index_demo_data() is True

    assert len(captured) == 1
    assert isinstance(captured[0], NL2SQLContext)


def test_index_demo_data_context_points_at_demo_config(demo_project, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(
        "nl2sql_cli.commands.indexing.run_indexing", captured.append
    )

    assert demo_project.index_demo_data() is True

    ctx = captured[0]
    assert sorted(a.datasource_id for a in ctx.ds_registry.list_adapters()) == [
        "manufacturing_history",
        "manufacturing_ops",
        "manufacturing_ref",
        "manufacturing_supply",
    ]
    assert str(tmp_path) in str(ctx.vector_store.persist_directory)


def test_index_demo_data_reports_missing_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = DemoManager(Console(), tmp_path)

    assert manager.index_demo_data() is False
