"""Index health: judged by what the index contains, not by whether a folder exists.

The owner's demo folder had ``data/vector_store_demo`` on disk and 0 entries in
it. Every check here reads the collection and the schema snapshot store.
"""
from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.indexing.health import inspect_index_at, inspect_vector_store
from nl2sql.indexing.vector_store import VectorStore


class _Snapshots:
    def __init__(self, versions):
        self.versions = versions

    def get_latest_version(self, ds_id):
        return self.versions.get(ds_id)


def _store(tmp_path):
    return VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))


def _fill(store, ds="chinook", version="v1", tables=2, columns=3):
    docs = [Document(page_content="ds", metadata={"type": "schema.datasource", "datasource_id": ds, "schema_version": version})]
    docs += [Document(page_content=f"t{i}", metadata={"type": "schema.table", "datasource_id": ds, "schema_version": version}) for i in range(tables)]
    docs += [Document(page_content=f"c{i}", metadata={"type": "schema.column", "datasource_id": ds, "schema_version": version}) for i in range(columns)]
    store.vectorstore.add_documents(docs)


def test_an_empty_collection_is_reported_empty(tmp_path):
    health = inspect_vector_store(_store(tmp_path), _Snapshots({"chinook": "v1"}), ["chinook"])

    assert health.status == "empty"
    assert health.total == 0
    assert "empty" in health.problems[0].lower()


def test_a_full_index_matching_the_snapshot_is_ok_with_counts_by_type(tmp_path):
    store = _store(tmp_path)
    _fill(store)

    health = inspect_vector_store(store, _Snapshots({"chinook": "v1"}), ["chinook"])

    assert health.status == "ok"
    assert health.counts == {"schema.column": 3, "schema.datasource": 1, "schema.table": 2}
    assert health.total == 6
    assert health.datasources[0].index_version == "v1"
    assert health.datasources[0].snapshot_version == "v1"
    assert health.problems == []


def test_an_index_older_than_the_latest_snapshot_is_stale(tmp_path):
    store = _store(tmp_path)
    _fill(store, version="v1")

    health = inspect_vector_store(store, _Snapshots({"chinook": "v2"}), ["chinook"])

    assert health.status == "stale"
    assert "v1" in health.problems[0] and "v2" in health.problems[0]


def test_a_configured_datasource_with_no_entries_is_stale(tmp_path):
    store = _store(tmp_path)
    _fill(store, ds="chinook")

    health = inspect_vector_store(store, _Snapshots({"chinook": "v1"}), ["chinook", "sales"])

    assert health.status == "stale"
    assert any("sales" in p for p in health.problems)


def test_to_dict_is_json_ready(tmp_path):
    store = _store(tmp_path)
    _fill(store)

    body = inspect_vector_store(store, _Snapshots({"chinook": "v1"}), ["chinook"]).to_dict()

    assert body["status"] == "ok"
    assert body["datasources"][0]["datasource_id"] == "chinook"


def test_inspecting_a_path_that_does_not_exist_reports_missing(tmp_path):
    health = inspect_index_at(tmp_path / "nope", "nl2sql_store", tmp_path / "schema.db", ["chinook"])

    assert health.status == "missing"
    assert not (tmp_path / "nope").exists()


def test_inspecting_an_existing_folder_with_an_empty_collection_reports_empty(tmp_path):
    """The incident: the folder exists, the collection holds nothing."""
    _store(tmp_path)  # creates the folder and an empty collection

    health = inspect_index_at(tmp_path / "vs", "nl2sql_store", tmp_path / "schema.db", ["chinook"])

    assert health.status == "empty"


def test_inspecting_a_healthy_folder_reads_the_snapshot_store(tmp_path):
    from nl2sql.schema.sqlite_store import SqliteSchemaStore

    store = _store(tmp_path)
    _fill(store, version="v9")
    snapshots = SqliteSchemaStore(tmp_path / "schema.db")
    snapshots._connection.execute(
        "INSERT INTO schema_snapshots VALUES ('chinook', 'v9', 'f', '{}', '{}', 1)"
    )
    snapshots._connection.commit()
    snapshots.close()

    health = inspect_index_at(tmp_path / "vs", "nl2sql_store", tmp_path / "schema.db", ["chinook"])

    assert health.status == "ok", health.problems
    assert health.datasources[0].snapshot_version == "v9"


class _Chunk:
    def __init__(self, kind, text):
        self.type = kind
        self._text = text

    def get_page_content(self):
        return self._text

    def get_metadata(self):
        return {"type": self.type, "datasource_id": "chinook", "schema_version": "v3", "table": self._text}


def test_only_the_active_build_is_counted_and_its_build_time_and_model_reported(tmp_path):
    store = _store(tmp_path)
    store.refresh_schema_chunks("chinook", "v3", [_Chunk("schema.datasource", "d"), _Chunk("schema.table", "t")], [])
    # A build that was written but never switched in (a crash before the switch).
    store.vectorstore.add_documents([Document(page_content="orphan", metadata={
        "type": "schema.table", "datasource_id": "chinook", "schema_version": "v4", "build_id": "dead"})])

    health = inspect_vector_store(store, _Snapshots({"chinook": "v3"}), ["chinook"])

    assert health.status == "ok", health.problems
    assert health.total == 2
    assert health.datasources[0].built_at
    assert health.built_at == health.datasources[0].built_at
    assert health.embedding_model == "FakeEmbeddings/unknown"


def test_a_different_configured_model_is_reported_with_the_full_rebuild_hint(tmp_path):
    store = _store(tmp_path)
    store.refresh_schema_chunks("chinook", "v3", [_Chunk("schema.datasource", "d")], [])

    class _Other(FakeEmbeddings):
        model: str = "other"

    other = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=_Other(size=8))
    health = inspect_vector_store(other, _Snapshots({"chinook": "v3"}), ["chinook"])

    assert health.status == "stale"
    assert any("--full" in p for p in health.problems)
