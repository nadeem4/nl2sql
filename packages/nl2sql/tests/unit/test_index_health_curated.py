"""A ``curated.yaml`` edited since the last index is a warning, not a failure.

The stale entries still point at queries that exist, and the matcher re-reads
every query from the loaded file, so questions keep working: ``ok`` stays true
and the warning names the datasource and the cheap fix.
"""
from __future__ import annotations

from langchain_core.embeddings import FakeEmbeddings

from nl2sql.config.curated import CuratedDatasource
from nl2sql.indexing.curated import CuratedChunkBuilder, curated_hash
from nl2sql.indexing.health import inspect_index_at, inspect_vector_store, summarize
from nl2sql.indexing.vector_store import VectorStore


class _Snapshots:
    def get_latest_version(self, ds_id):
        return "v1"


class _Chunk:
    type = "schema.datasource"

    def __init__(self, ds):
        self.ds = ds

    def get_page_content(self):
        return f"datasource {self.ds}"

    def get_metadata(self):
        return {"type": self.type, "datasource_id": self.ds, "schema_version": "v1"}


SECTION = CuratedDatasource.model_validate({"verified_queries": [
    {"id": "top_customers", "description": "Customers by revenue", "questions": ["Top customers"], "sql": "SELECT 1"},
]})


def _store(tmp_path):
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
    chunks = [_Chunk("chinook")] + CuratedChunkBuilder("chinook", SECTION, "v1").build()
    store.refresh_schema_chunks("chinook", "v1", chunks, [], curated_hash=curated_hash(SECTION))
    return store


def test_an_unchanged_curated_file_gives_no_warning(tmp_path):
    health = inspect_vector_store(_store(tmp_path), _Snapshots(), ["chinook"],
                                  curated_hashes={"chinook": curated_hash(SECTION)})

    assert health.ok
    assert health.warnings == []
    assert health.counts["curated.verified_query"] == 2


def test_a_changed_curated_file_is_a_warning_and_ok_stays_true(tmp_path):
    edited = SECTION.model_copy(deep=True)
    edited.verified_queries[0].questions.append("Best customers")

    health = inspect_vector_store(_store(tmp_path), _Snapshots(), ["chinook"],
                                  curated_hashes={"chinook": curated_hash(edited)})

    assert health.ok
    assert health.status == "ok"
    assert health.problems == []
    [warning] = health.warnings
    assert "curated.yaml changed for 'chinook'" in warning
    assert "nl2sql index --curated-only" in warning
    assert health.to_dict()["warnings"] == [warning]


def test_an_index_from_before_curated_answers_with_no_file_gives_no_warning():
    metadatas = [{"type": "schema.datasource", "datasource_id": "chinook", "schema_version": "v1"}]

    health = summarize(metadatas, lambda _ds: "v1", ["chinook"], collection_metadata={},
                       curated_hashes={"chinook": curated_hash(None)})

    assert health.ok and health.warnings == []


def test_an_index_from_before_curated_answers_with_a_file_warns():
    metadatas = [{"type": "schema.datasource", "datasource_id": "chinook", "schema_version": "v1"}]

    health = summarize(metadatas, lambda _ds: "v1", ["chinook"], collection_metadata={},
                       curated_hashes={"chinook": curated_hash(SECTION)})

    assert health.ok and len(health.warnings) == 1


def test_without_hashes_nothing_is_compared(tmp_path):
    health = inspect_vector_store(_store(tmp_path), _Snapshots(), ["chinook"])

    assert health.warnings == []


def test_the_on_disk_check_compares_too(tmp_path):
    store = _store(tmp_path)

    health = inspect_index_at(tmp_path / "vs", store.collection_name, tmp_path / "none.db", ["chinook"],
                              curated_hashes={"chinook": curated_hash(None)})

    assert health.ok
    assert len(health.warnings) == 1
