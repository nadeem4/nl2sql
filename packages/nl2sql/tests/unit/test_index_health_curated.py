"""A ``curated.yaml`` edited since the last index is a warning, not a failure.

The stale entries still point at queries that exist, and the matcher re-reads
every query from the loaded file, so questions keep working: ``ok`` stays true
and the warning names the datasource and the cheap fix. Each build records
its hash in its ``BuildPointer``.
"""
from __future__ import annotations

from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.config.curated import CuratedDatasource
from nl2sql.indexing.curated import CuratedChunkBuilder, curated_hash
from nl2sql.indexing.health import index_health


class _Snapshots:
    def get_snapshot(self, ds_id, version):
        return object()


SECTION = CuratedDatasource.model_validate({"verified_queries": [
    {"id": "top_customers", "description": "Customers by revenue", "questions": ["Top customers"], "sql": "SELECT 1"},
]})


def _index(tmp_path, section=SECTION):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    chunks = [Chunk("datasource chinook", type="schema.datasource", datasource_id="chinook")]
    chunks += CuratedChunkBuilder("chinook", section, "v1").build()
    index.write("chinook", chunks, curated_hash=curated_hash(section) if section is not None else None)
    return index


def _health(index, **kwargs):
    return index_health(index.index, index.namespace, _Snapshots(), ["chinook"], **kwargs)


def test_an_unchanged_curated_file_gives_no_warning(tmp_path):
    health = _health(_index(tmp_path), curated_hashes={"chinook": curated_hash(SECTION)})

    assert health.ok
    assert health.warnings == []
    assert health.counts["curated.verified_query"] == 2


def test_a_changed_curated_file_is_a_warning_and_ok_stays_true(tmp_path):
    edited = SECTION.model_copy(deep=True)
    edited.verified_queries[0].questions.append("Best customers")

    health = _health(_index(tmp_path), curated_hashes={"chinook": curated_hash(edited)})

    assert health.ok
    assert health.status == "ok"
    assert health.problems == []
    [warning] = health.warnings
    assert "curated.yaml changed for 'chinook'" in warning
    assert "nl2sql index --refresh" in warning
    assert health.to_dict()["warnings"] == [warning]


def test_a_build_without_curated_answers_and_no_file_gives_no_warning(tmp_path):
    health = _health(_index(tmp_path, section=None), curated_hashes={"chinook": curated_hash(None)})

    assert health.ok and health.warnings == []


def test_a_build_without_curated_answers_and_a_file_warns(tmp_path):
    health = _health(_index(tmp_path, section=None), curated_hashes={"chinook": curated_hash(SECTION)})

    assert health.ok and len(health.warnings) == 1


def test_without_hashes_nothing_is_compared(tmp_path):
    health = _health(_index(tmp_path))

    assert health.warnings == []
