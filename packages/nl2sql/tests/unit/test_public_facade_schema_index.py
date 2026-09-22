"""The facade methods the playground and nl2sql-api share: schema, index health,
rebuild, retrieval inspection and reloading the LLM config.

Each runs over a small fake context, so no database, model or key is needed.
"""
import pytest
import yaml
from langchain_core.embeddings import FakeEmbeddings

from nl2sql import NL2SQL
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.llm.registry import LLMRegistry
from nl2sql.secrets import SecretManager
from nl2sql_adapter_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


class _Registry:
    def __init__(self, ids=("chinook",)):
        self._ids = list(ids)

    def list_ids(self):
        return list(self._ids)


class _SchemaStore:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def get_latest_snapshot(self, _ds):
        return self.snapshot

    def get_latest_version(self, _ds):
        return "v1" if self.snapshot else None


class _Context:
    def __init__(self, vector_store=None, snapshot=None, llm_registry=None):
        self.vector_store = vector_store
        self.schema_store = _SchemaStore(snapshot)
        self.ds_registry = _Registry()
        self.llm_registry = llm_registry


def _engine(ctx) -> NL2SQL:
    engine = NL2SQL.__new__(NL2SQL)
    engine._ctx = ctx
    return engine


def _snapshot() -> SchemaSnapshot:
    album = TableRef(schema_name="main", table_name="Album")
    artist = TableRef(schema_name="main", table_name="Artist")
    contract = SchemaContract(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={
            album.full_name: TableContract(
                table=album,
                columns={
                    "AlbumId": ColumnContract(name="AlbumId", data_type="INTEGER",
                                              is_nullable=False, is_primary_key=True),
                    "ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER", is_nullable=False),
                },
                foreign_keys=[ForeignKeyContract(constrained_columns=["ArtistId"], referred_table=artist,
                                                 referred_columns=["ArtistId"])],
            ),
            artist.full_name: TableContract(
                table=artist,
                columns={"ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER",
                                                    is_nullable=False, is_primary_key=True)},
            ),
        },
    )
    metadata = SchemaMetadata(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={album.full_name: TableMetadata(table=album, row_count=347, description="Albums",
                                               columns={"AlbumId": ColumnMetadata(description="Album key")})},
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_get_schema_projects_the_indexed_snapshot():
    schema = _engine(_Context(snapshot=_snapshot())).get_schema("chinook")

    assert schema["datasource_id"] == "chinook"
    assert [t["name"] for t in schema["tables"]] == ["Album", "Artist"]
    album = schema["tables"][0]
    assert album["row_count"] == 347 and album["description"] == "Albums"
    assert album["columns"][0] == {"name": "AlbumId", "type": "INTEGER", "nullable": False,
                                   "primary_key": True, "description": "Album key"}
    assert album["foreign_keys"] == [{"columns": ["ArtistId"], "references_table": "Artist",
                                      "references_columns": ["ArtistId"]}]


def test_get_schema_before_the_first_index_has_no_tables():
    assert _engine(_Context()).get_schema("chinook") == {"datasource_id": "chinook", "tables": []}


def test_index_health_without_a_vector_store_is_missing():
    health = _engine(_Context()).index_health()
    assert health["status"] == "missing"
    assert health["problems"]


def test_index_health_reads_the_live_store(tmp_path):
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
    health = _engine(_Context(vector_store=store, snapshot=_snapshot())).index_health()
    assert health["status"] in {"missing", "empty", "stale", "ok"}
    assert "datasources" in health and "counts" in health


def test_rebuild_index_hands_the_engine_context_to_the_rebuild(monkeypatch):
    seen = {}

    def _rebuild(ctx, **kwargs):
        seen["ctx"], seen["kwargs"] = ctx, kwargs
        return "result"

    monkeypatch.setattr("nl2sql.indexing.rebuild.rebuild_index", _rebuild)
    ctx = _Context()
    assert _engine(ctx).rebuild_index(datasource_ids=["chinook"]) == "result"
    assert seen["ctx"] is ctx
    # Enrichment spends tokens, so it is off unless asked for.
    assert seen["kwargs"]["enrich"] is False
    assert seen["kwargs"]["datasource_ids"] == ["chinook"]


def test_inspect_retrieval_runs_the_stores_mmr_search(tmp_path):
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
    record = _engine(_Context(vector_store=store)).inspect_retrieval("albums", k=3)
    assert isinstance(record, dict)


def test_inspect_retrieval_without_a_vector_store_says_so():
    with pytest.raises(LookupError, match="no vector index"):
        _engine(_Context()).inspect_retrieval("albums")


def test_reload_llm_config_replaces_every_agent(tmp_path):
    path = tmp_path / "llm.yaml"

    def _write(agents):
        path.write_text(yaml.safe_dump({
            "version": 1,
            "default": {"provider": "openai", "model": "gpt-5.4", "api_key": "sk-test-000000000000000000"},
            "agents": agents,
        }), encoding="utf-8")

    registry = LLMRegistry(SecretManager())
    engine = _engine(_Context(llm_registry=registry))
    _write({"astplanner": {"provider": "openai", "model": "gpt-4.1", "api_key": "sk-test-000000000000000000"}})
    engine.reload_llm_config(path)
    assert set(registry.list_llms()) == {"default", "astplanner"}

    _write({})
    engine.reload_llm_config(path)
    assert set(registry.list_llms()) == {"default"}
