"""Index health: judged by what the live builds contain, not by whether a folder exists.

The owner's demo folder had ``data/vector_store_demo`` on disk and 0 entries in
it. Every check here reads the index store's namespace, its pointers and the
snapshots they name.
"""
from __future__ import annotations

from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.indexing.health import index_health
from nl2sql_sdk.stores import Entry


class _Snapshots:
    """A state store that holds the given ``(datasource, version)`` snapshots."""

    def __init__(self, *held):
        self.held = set(held)

    def get_snapshot(self, ds_id, version):
        return object() if (ds_id, version) in self.held else None


def _index(tmp_path):
    return TestIndex(tmp_path, FakeEmbeddings(size=8))


def _chunks(ds="chinook", tables=2, columns=3):
    return ([Chunk("ds", type="schema.datasource", datasource_id=ds)]
            + [Chunk(f"t{i}", type="schema.table", datasource_id=ds) for i in range(tables)]
            + [Chunk(f"c{i}", type="schema.column", datasource_id=ds) for i in range(columns)])


def _health(index, snapshots, ids=("chinook",), **kwargs):
    return index_health(index.index, index.namespace, snapshots, list(ids), **kwargs)


def test_a_model_with_no_namespace_yet_is_missing_and_says_run_index(tmp_path):
    index = _index(tmp_path)

    health = _health(index, _Snapshots(), embedding_model="local/all-MiniLM-L6-v2")

    assert health.status == "missing"
    assert "local/all-MiniLM-L6-v2" in health.problems[0] and "nl2sql index" in health.problems[0]
    assert not (tmp_path / "index").exists()


def test_a_namespace_with_no_live_build_is_empty(tmp_path):
    index = _index(tmp_path)
    index.index.open(index.namespace, 8)

    health = _health(index, _Snapshots())

    assert health.status == "empty"
    assert health.total == 0
    assert "empty" in health.problems[0].lower()


def test_a_live_build_is_ok_with_counts_by_type_and_its_schema_version(tmp_path):
    index = _index(tmp_path)
    index.write("chinook", _chunks(), schema_version="v1")

    health = _health(index, _Snapshots(("chinook", "v1")), embedding_model="test/test")

    assert health.status == "ok", health.problems
    assert health.counts == {"schema.column": 3, "schema.datasource": 1, "schema.table": 2}
    assert health.total == 6
    assert health.datasources[0].index_version == "v1"
    assert health.datasources[0].built_at and health.built_at == health.datasources[0].built_at
    assert (health.embedding_model, health.namespace) == ("test/test", index.namespace)
    assert health.problems == []


def test_a_build_whose_snapshot_is_gone_is_stale(tmp_path):
    index = _index(tmp_path)
    index.write("chinook", _chunks(), schema_version="v1")

    health = _health(index, _Snapshots())

    assert health.status == "stale"
    assert "v1" in health.problems[0]


def test_a_configured_datasource_with_no_build_is_stale(tmp_path):
    index = _index(tmp_path)
    index.write("chinook", _chunks())

    health = _health(index, _Snapshots(("chinook", "v1")), ids=("chinook", "sales"))

    assert health.status == "stale"
    assert any("sales" in p for p in health.problems)


def test_only_the_live_build_is_counted(tmp_path):
    index = _index(tmp_path)
    index.write("chinook", _chunks(tables=1, columns=0))
    # A build that was written but never switched in (a crash before the switch).
    index.index.add(index.namespace, [Entry(id="orphan", text="orphan", vector=[0.1] * 8, metadata={
        "type": "schema.table", "datasource_id": "chinook", "build_id": "dead"})])

    health = _health(index, _Snapshots(("chinook", "v1")))

    assert health.status == "ok", health.problems
    assert health.total == 2


def test_to_dict_is_json_ready(tmp_path):
    index = _index(tmp_path)
    index.write("chinook", _chunks())

    body = _health(index, _Snapshots(("chinook", "v1"))).to_dict()

    assert body["status"] == "ok"
    assert body["datasources"][0]["datasource_id"] == "chinook"
