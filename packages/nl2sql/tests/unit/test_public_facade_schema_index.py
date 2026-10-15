"""The namespace methods the playground and nl2sql-api share: the schema, the
index's health, building it and inspecting retrieval.

Each runs over a small fake context, so no database, model or key is needed.
"""
import pytest
import yaml
from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings

from nl2sql import NL2SQL, Policies, UserContext
from nl2sql.llm.registry import LLMRegistry
from nl2sql.policies import PolicyEngine
from nl2sql.secrets import SecretManager
from nl2sql_sdk.schema import (
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

    def unavailable(self):
        return {}

    def get_preferred_tables(self, _ds):
        return []


class _SchemaStore:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def get_snapshot(self, _ds, _version):
        return self.snapshot


class _Context:
    """The parts of the runtime these namespaces reach, over a real index store in ``tmp_path``.

    With ``snapshot``, chinook is indexed at version v1 and the state store holds it.
    """

    def __init__(self, tmp_path, snapshot=None, llm_registry=None):
        self.state = _SchemaStore(snapshot)
        self.test_index = TestIndex(tmp_path, FakeEmbeddings(size=8), state=self.state)
        for key, value in vars(self.test_index.ctx).items():
            setattr(self, key, value)
        if snapshot is not None:
            self.test_index.write("chinook", [Chunk("Datasource: chinook", type="schema.datasource",
                                                    datasource_id="chinook")])
        self.ds_registry = _Registry()
        # admin reads everything; viewer reads chinook but not Artist.
        self.policy_engine = PolicyEngine(Policies(roles={
            "admin": {"description": "Everything", "allow": ["*"]},
            "viewer": {"description": "No artists", "allow": ["chinook"], "deny": ["chinook.*.Artist"]},
        }))
        self.llm_registry = llm_registry
        self.curated = None

    @property
    def vector_store(self):
        return self.test_index.retriever()


def _engine(ctx) -> NL2SQL:
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = ctx
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


ADMIN = UserContext(roles=["admin"])


def test_datasources_schema_projects_the_indexed_snapshot(tmp_path):
    schema = _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.schema("chinook", user_context=ADMIN)

    assert schema["datasource_id"] == "chinook"
    assert [t["name"] for t in schema["tables"]] == ["Album", "Artist"]
    album = schema["tables"][0]
    assert album["row_count"] == 347 and album["description"] == "Albums"
    assert album["columns"][0] == {"name": "AlbumId", "type": "INTEGER", "nullable": False,
                                   "primary_key": True, "description": "Album key"}
    assert album["foreign_keys"] == [{"columns": ["ArtistId"], "references_table": "Artist",
                                      "references_columns": ["ArtistId"]}]


def test_datasources_schema_before_the_first_index_has_no_tables(tmp_path):
    assert _engine(_Context(tmp_path)).datasources.schema("chinook", user_context=ADMIN) == {
        "datasource_id": "chinook", "tables": []}


def test_schema_requires_a_caller(tmp_path):
    """No default view (B29, D33): an app that forgot the caller fails rather than seeing everything."""
    with pytest.raises(TypeError):
        _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.schema("chinook")


def test_schema_with_a_caller_drops_denied_tables(tmp_path):
    """A denied table is left out, and so is a foreign key that names it."""
    engine = _engine(_Context(tmp_path, snapshot=_snapshot()))

    schema = engine.datasources.schema("chinook", user_context=UserContext(roles=["viewer"]))

    assert [t["name"] for t in schema["tables"]] == ["Album"]
    assert schema["tables"][0]["foreign_keys"] == []
    assert "'Artist'" not in str(schema)


@pytest.mark.parametrize("caller", [UserContext(), UserContext(roles=["nobody"])])
def test_schema_for_a_caller_with_no_known_role_has_no_tables(tmp_path, caller):
    engine = _engine(_Context(tmp_path, snapshot=_snapshot()))

    assert engine.datasources.schema("chinook", user_context=caller)["tables"] == []


def test_index_health_before_the_first_index_is_missing(tmp_path):
    health = _engine(_Context(tmp_path)).index.health()
    assert health["status"] == "missing"
    assert health["problems"]


def test_index_health_reads_the_live_store(tmp_path):
    health = _engine(_Context(tmp_path, snapshot=_snapshot())).index.health()
    assert health["status"] == "ok", health["problems"]
    assert health["datasources"][0]["index_version"] == "v1" and health["counts"]


def test_index_build_hands_the_runtime_to_the_rebuild(monkeypatch):
    seen = {}

    def _rebuild(ctx, **kwargs):
        seen["ctx"], seen["kwargs"] = ctx, kwargs
        return "result"

    monkeypatch.setattr("nl2sql.indexing.rebuild.rebuild_index", _rebuild)
    ctx = object()
    assert _engine(ctx).index.build(["chinook"]) == "result"
    assert seen["ctx"] is ctx
    # Enrichment spends tokens, so it is off unless asked for.
    assert seen["kwargs"]["enrich"] is False
    assert seen["kwargs"]["datasource_ids"] == ["chinook"]


def test_index_inspect_runs_the_stores_mmr_search(tmp_path):
    record = _engine(_Context(tmp_path, snapshot=_snapshot())).index.inspect("albums", k=3)
    assert [e["datasource_id"] for e in record["pool"]] == ["chinook"]
