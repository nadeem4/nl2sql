"""``nl2sql index`` has to tell a script whether the index is usable.

A partially populated index silently produces wrong answers later, so any
datasource failure - not just a total wipeout - exits non-zero.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.cli.commands import indexing as indexing_cmd


class _StubAdapter:
    def __init__(self, datasource_id: str):
        self.datasource_id = datasource_id


class _StubStore:
    """Records what the rebuild did with the live index."""

    persist_directory = "/tmp/vs"

    def __init__(self):
        self.promoted = False
        self.discarded = False
        self.embeddings = SimpleNamespace(embed_query=lambda _text: [0.0])

    def create_staging(self):
        return self

    def check_embedding_model(self):
        return None

    def discard(self):
        self.discarded = True

    def promote(self, staging):
        self.promoted = True


def _context(*datasource_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        vector_store=_StubStore(),
        ds_registry=SimpleNamespace(
            list_adapters=lambda: [_StubAdapter(ds) for ds in datasource_ids]
        ),
    )


@pytest.fixture()
def orchestrator(monkeypatch):
    """Replaces the real orchestrator with one driven by a per-datasource script."""
    outcomes: dict = {}
    indexed: list = []
    outcomes["_indexed"] = indexed

    class _StubOrchestrator:
        def __init__(self, ctx, enrich=True):
            self.ctx = ctx

        def index_datasource(self, adapter, vector_store=None, switch_guard=None):
            indexed.append(adapter.datasource_id)
            outcome = outcomes[adapter.datasource_id]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr("nl2sql.indexing.rebuild.IndexingOrchestrator", _StubOrchestrator)
    return outcomes


def test_all_datasources_failing_exits_non_zero(orchestrator, capsys):
    orchestrator["ds_a"] = RuntimeError("no key")
    orchestrator["ds_b"] = RuntimeError("no key")

    with pytest.raises(SystemExit) as exit_info:
        indexing_cmd.run_indexing(_context("ds_a", "ds_b"))

    assert exit_info.value.code == 1
    assert "Indexing completed with errors" in capsys.readouterr().out


def test_a_single_failure_exits_non_zero_and_keeps_the_previous_index(orchestrator, capsys):
    orchestrator["ds_a"] = {"datasource_id": "ds_a", "schema_version": "v1", "table": 3}
    orchestrator["ds_b"] = RuntimeError("connection refused")
    ctx = _context("ds_a", "ds_b")

    with pytest.raises(SystemExit) as exit_info:
        indexing_cmd.run_indexing(ctx)

    assert exit_info.value.code == 1
    assert "keeps its previous index entries" in " ".join(capsys.readouterr().out.split())


def test_full_success_exits_zero(orchestrator, capsys):
    orchestrator["ds_a"] = {"datasource_id": "ds_a", "schema_version": "v1", "table": 3}

    ctx = _context("ds_a")
    indexing_cmd.run_indexing(ctx)

    assert "Indexing complete." in capsys.readouterr().out


def test_indexing_one_datasource_indexes_only_that_one(orchestrator):
    orchestrator["ds_b"] = {"datasource_id": "ds_b", "schema_version": "v1", "table": 3}

    indexing_cmd.run_indexing(_context("ds_a", "ds_b", "ds_c"), datasource_ids=["ds_b"])

    assert orchestrator["_indexed"] == ["ds_b"]


def test_an_unknown_datasource_exits_non_zero(orchestrator, capsys):
    with pytest.raises(SystemExit) as exit_info:
        indexing_cmd.run_indexing(_context("ds_a"), datasource_ids=["nope"])

    assert exit_info.value.code == 1
    assert "nope" in capsys.readouterr().out


def test_the_index_command_passes_datasource_and_full(monkeypatch):
    from typer.testing import CliRunner

    from nl2sql.cli import main

    seen = {}
    monkeypatch.setattr(main, "NL2SQLContext", lambda *a, **k: "ctx")
    monkeypatch.setattr(main, "run_indexing", lambda ctx, **kw: seen.update(kw))

    result = CliRunner().invoke(main.app, ["index", "--datasource", "chinook", "--datasource", "sales"])
    assert result.exit_code == 0, result.output
    assert seen == {"datasource_ids": ["chinook", "sales"], "full": False}

    CliRunner().invoke(main.app, ["index", "--full"])
    assert seen == {"datasource_ids": None, "full": True}
