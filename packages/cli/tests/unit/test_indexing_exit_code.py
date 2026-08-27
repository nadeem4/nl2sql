"""``nl2sql index`` has to tell a script whether the index is usable.

A partially populated index silently produces wrong answers later, so any
datasource failure - not just a total wipeout - exits non-zero.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql_cli.commands import indexing as indexing_cmd


class _StubAdapter:
    def __init__(self, datasource_id: str):
        self.datasource_id = datasource_id


def _context(*datasource_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        vector_store=SimpleNamespace(persist_directory="/tmp/vs"),
        ds_registry=SimpleNamespace(
            list_adapters=lambda: [_StubAdapter(ds) for ds in datasource_ids]
        ),
    )


@pytest.fixture()
def orchestrator(monkeypatch):
    """Replaces the real orchestrator with one driven by a per-datasource script."""
    outcomes: dict = {}

    class _StubOrchestrator:
        def __init__(self, ctx):
            self.ctx = ctx

        def clear_store(self):
            return None

        def index_datasource(self, adapter):
            outcome = outcomes[adapter.datasource_id]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(indexing_cmd, "IndexingOrchestrator", _StubOrchestrator)
    return outcomes


def test_all_datasources_failing_exits_non_zero(orchestrator, capsys):
    orchestrator["ds_a"] = RuntimeError("no key")
    orchestrator["ds_b"] = RuntimeError("no key")

    with pytest.raises(SystemExit) as exit_info:
        indexing_cmd.run_indexing(_context("ds_a", "ds_b"))

    assert exit_info.value.code == 1
    assert "Indexing completed with errors" in capsys.readouterr().out


def test_a_single_failure_exits_non_zero(orchestrator):
    orchestrator["ds_a"] = {"datasource_id": "ds_a", "schema_version": "v1", "table": 3}
    orchestrator["ds_b"] = RuntimeError("connection refused")

    with pytest.raises(SystemExit) as exit_info:
        indexing_cmd.run_indexing(_context("ds_a", "ds_b"))

    assert exit_info.value.code == 1


def test_full_success_exits_zero(orchestrator, capsys):
    orchestrator["ds_a"] = {"datasource_id": "ds_a", "schema_version": "v1", "table": 3}

    indexing_cmd.run_indexing(_context("ds_a"))

    assert "Indexing complete." in capsys.readouterr().out
