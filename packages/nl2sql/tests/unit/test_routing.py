"""Phase R routing (wave_a.md, section 9, row R1).

One index entry per example phrasing; five signals (the description, the
example questions, the verified queries, the tables and the columns) each
ranking datasources by their best entry, fused with reciprocal rank fusion;
the floor and the ``candidates`` cut; the scope filter before the search; the
resolver forwarding at most ``max_datasources``; and the retrieval ``k``
values read from ``settings.retrieval``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from index_helpers import Chunk, TestIndex  # same directory
from test_retrieval_trace import BagEmbeddings  # same directory

from nl2sql.common.errors import ErrorCode
from nl2sql.indexing.chunk_builder import SchemaChunkBuilder, build_example_chunks
from nl2sql.indexing.models import RouteCandidate
from nl2sql.indexing.retrieval import ROUTE_SIGNALS, Retriever, fuse, shortlist
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.policies import UserContext
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.schema import (
    ColumnContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

FAKE_KEY = "-".join(["fake", "key", "for", "tests"])


# ------------------------------------------------------------ one entry per phrasing


def _snapshot() -> SchemaSnapshot:
    ref = TableRef(schema_name="main", table_name="Artist")
    contract = SchemaContract(datasource_id="chinook", engine_type="sqlite", tables={
        ref.full_name: TableContract(table=ref, columns={"ArtistId": ColumnContract(name="ArtistId",
                                                                                     data_type="INTEGER")})})
    metadata = SchemaMetadata(datasource_id="chinook", engine_type="sqlite", description="A music store",
                              domains=["music"], tables={ref.full_name: TableMetadata(table=ref, columns={})})
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_each_sample_question_is_its_own_entry():
    questions = ["Which artist has the most albums?", "Which genre sells the most tracks?"]

    chunks = SchemaChunkBuilder("chinook", _snapshot(), "v1", questions).build()

    examples = [c for c in chunks if c.type == "schema.datasource_example"]
    assert [c.get_page_content() for c in examples] == questions
    assert all(c.get_metadata()["datasource_id"] == "chinook" for c in examples)
    assert len({c.id for c in examples}) == 2
    [datasource] = [c for c in chunks if c.type == "schema.datasource"]
    # The datasource entry holds the description and domains only now.
    assert "A music store" in datasource.get_page_content()
    assert not any(q in datasource.get_page_content() for q in questions)


def test_the_example_builder_takes_its_questions_from_the_caller():
    """Wave B adds the glossary's ``examples`` to the list; the builder needs no change for it."""
    chunks = build_example_chunks("chinook", ["From sample_questions?", "From a glossary?", "  ",
                                              "From sample_questions?"], "v1")

    assert [c.question for c in chunks] == ["From sample_questions?", "From a glossary?"]
    assert all(c.id.startswith("schema.datasource_example:chinook:") and c.id.endswith(":v1") for c in chunks)
    # Stable: the same question gets the same id on every build.
    assert [c.id for c in build_example_chunks("chinook", ["From a glossary?"], "v1")] == [chunks[1].id]


# ------------------------------------------------------------ fusion


def test_rrf_fuses_signal_ranks():
    fused = fuse({"description": ["a", "b"], "tables": ["b", "a"], "columns": ["b"]}, rrf_k=60)

    assert [c.datasource_id for c in fused] == ["b", "a"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0].ranks == {"description": 2, "tables": 1, "columns": 1}


def test_floor_ratio_drops_weak_candidates():
    fused = [RouteCandidate(datasource_id="a", score=1.0), RouteCandidate(datasource_id="b", score=0.6),
             RouteCandidate(datasource_id="c", score=0.4)]

    kept, dropped = shortlist(fused, limit=5, floor_ratio=0.5)

    assert [c.datasource_id for c in kept] == ["a", "b"]
    assert dropped == [{"datasource_id": "c", "reason": "floor"}]
    kept, dropped = shortlist(fused, limit=1, floor_ratio=0.0)
    assert [c.datasource_id for c in kept] == ["a"]
    assert [d["reason"] for d in dropped] == ["limit", "limit"]


# ------------------------------------------------------------ the retriever's five signals


def _index(tmp_path) -> Retriever:
    """chinook has tables about tracks and genres; hr has employee tables; neither has example questions."""
    index = TestIndex(tmp_path, BagEmbeddings())
    index.write("chinook", [
        Chunk("a store", type="schema.datasource", datasource_id="chinook", id="ds:chinook"),
        Chunk("genre track", type="schema.table", datasource_id="chinook", table="Genre", id="t:chinook:Genre"),
        Chunk("track name price", type="schema.column", datasource_id="chinook", table="Track", column="Name",
              id="c:chinook:Track.Name"),
    ])
    index.write("hr", [
        Chunk("a company", type="schema.datasource", datasource_id="hr", id="ds:hr"),
        Chunk("employee", type="schema.table", datasource_id="hr", table="Employee", id="t:hr:Employee"),
        Chunk("employee name", type="schema.column", datasource_id="hr", table="Employee", column="Name",
              id="c:hr:Employee.Name"),
    ])
    return index.retriever()


def test_table_votes_route_a_question_without_examples(tmp_path):
    retriever = _index(tmp_path)

    candidates = retriever.route("Which genre has the most tracks?", ["chinook", "hr"])

    assert candidates[0].datasource_id == "chinook"
    assert candidates[0].ranks["tables"] == 1
    assert "examples" not in candidates[0].ranks


def test_example_entries_vote(tmp_path):
    index = TestIndex(tmp_path, BagEmbeddings())
    for ds in ("chinook", "hr"):
        index.write(ds, [Chunk("data", type="schema.datasource", datasource_id=ds, id=f"ds:{ds}")])
    index.write("hr", [
        Chunk("data", type="schema.datasource", datasource_id="hr", id="ds:hr"),
        Chunk("Which employee has the highest price?", type="schema.datasource_example", datasource_id="hr",
              id="ex:hr:1"),
    ])

    candidates = index.retriever().route("employee price", ["chinook", "hr"])

    assert candidates[0].datasource_id == "hr"
    assert candidates[0].ranks["examples"] == 1


def test_denied_datasource_never_ranks(tmp_path):
    retriever = _index(tmp_path)
    explain: list = []

    candidates = retriever.route("Which genre has the most tracks?", ["hr"], explain=explain)

    assert [c.datasource_id for c in candidates] == ["hr"]
    seen = {entry["datasource_id"] for record in explain for entry in record.get("pool", [])}
    assert seen == {"hr"}


def test_routing_record_in_the_trace(tmp_path):
    retriever = _index(tmp_path)
    explain: list = []

    retriever.route("Which genre has the most tracks?", ["chinook", "hr"], explain=explain, limit=5,
                    floor_ratio=0.5)

    assert [r["search"] for r in explain] == [f"route.{s}" for s in ROUTE_SIGNALS] + ["routing"]
    routing = explain[-1]
    assert routing["rrf_k"] == Retriever.RRF_K and routing["vote_k"] == Retriever.VOTE_K
    assert routing["candidates"] == 5 and routing["floor_ratio"] == 0.5
    assert routing["signals"]["tables"][0] == "chinook"
    assert routing["fused"][0]["datasource_id"] == "chinook"
    assert routing["picks"][0] == "chinook"
    assert all("text" not in entry for record in explain for entry in record.get("pool", []))


# ------------------------------------------------------------ the resolver


@pytest.fixture
def judge():
    servers = []

    def _make(answerable):
        server = FakeLLMServer([Rule("AnswerabilityResponse", {"answerable_datasource_ids": answerable,
                                                               "reason": "ok"})]).start()
        servers.append(server)
        registry = LLMRegistry(SecretManager())
        registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                          base_url=server.base_url, name="default"))
        return registry

    yield _make
    for server in servers:
        server.stop()


class RouteStore:
    """A vector store whose ``route`` ranks the datasources it is asked about in ``order``."""

    def __init__(self, order):
        self.order = list(order)
        self.calls = []

    def route(self, question, datasource_ids, explain=None, *, limit=5, floor_ratio=0.5):
        self.calls.append({"datasource_ids": list(datasource_ids), "limit": limit, "floor_ratio": floor_ratio})
        ranked = [d for d in self.order if d in datasource_ids]
        return [RouteCandidate(datasource_id=d, score=1.0 / (i + 1)) for i, d in enumerate(ranked)][:limit]

    def schema_version(self, _ds):
        return "v1"

    def snapshot(self, _ds):
        return SimpleNamespace(
            metadata=SimpleNamespace(description="d"),
            contract=SimpleNamespace(tables={f"[main].[T{i}]": SimpleNamespace(
                table=SimpleNamespace(schema_name="main", table_name=f"T{i}")) for i in range(5)}))


def _resolver_ctx(registry, store, ids, allow=("*",), settings=None):
    return fake_runtime(
        vector_store=store, settings=settings,
        policies={"default_role": "r", "roles": {"r": {"description": "d", "allow": list(allow)}}},
        ds_registry=SimpleNamespace(list_ids=lambda: list(ids)), llm_registry=registry)


def test_resolver_forwards_at_most_max_datasources(judge):
    store = RouteStore(["d", "c", "b", "a"])
    ctx = _resolver_ctx(judge(["a", "b", "c", "d"]), store, ["a", "b", "c", "d"],
                        settings={"routing": {"max_datasources": 2, "candidates": 4, "floor_ratio": 0.1}})

    result = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))

    response = result["datasource_resolver_response"]
    # The two the fusion ranked best, not the first two by id.
    assert response.allowed_datasource_ids == ["c", "d"]
    assert [d.datasource_id for d in response.resolved_datasources] == ["c", "d"]
    assert store.calls == [{"datasource_ids": ["a", "b", "c", "d"], "limit": 4, "floor_ratio": 0.1}]


def test_resolver_routes_only_over_datasources_the_scope_reaches(judge):
    store = RouteStore(["secret", "open"])
    ctx = _resolver_ctx(judge(["open"]), store, ["open", "secret"], allow=["open"])

    result = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))

    assert store.calls[0]["datasource_ids"] == ["open"]
    assert result["datasource_resolver_response"].allowed_datasource_ids == ["open"]


def test_resolver_with_no_reachable_datasource_never_searches(judge):
    store = RouteStore(["a", "b"])
    ctx = _resolver_ctx(judge(["a"]), store, ["a", "b"], allow=[])

    result = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))

    assert store.calls == []
    assert result["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION


def test_retrieval_k_values_come_from_settings(judge):
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
    from nl2sql.pipeline.state import SubgraphExecutionState

    seen = {}
    snapshot = _snapshot()
    store = SimpleNamespace(
        snapshot=lambda _id: snapshot,
        retrieve_schema_context=lambda *_a, **k: seen.setdefault("table_k", k["k"]) and [
            SimpleNamespace(metadata={"table": "main.Artist"})],
        retrieve_planning_context=lambda *_a, **k: seen.setdefault("planning_k", k["k"]) and [],
        retrieve_column_candidates=lambda *_a, **_k: [],
    )
    ctx = fake_runtime(vector_store=store, state=SimpleNamespace(get_snapshot=lambda *_a: snapshot),
                       policies={"allow_all": True},
                       settings={"retrieval": {"full_snapshot_max_tables": 0, "table_k": 3, "planning_k": 7}})

    SchemaRetrieverNode(ctx)(SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="artists")))

    assert seen == {"table_k": 3, "planning_k": 7}

    # And the resolver's table list for the decomposer is capped by decomposer_tables.
    ctx = _resolver_ctx(judge(["a"]), RouteStore(["a", "b"]), ["a", "b"],
                        settings={"retrieval": {"decomposer_tables": 2}})
    result = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))
    [resolved] = result["datasource_resolver_response"].resolved_datasources
    assert resolved.tables == ["T0", "T1"] and resolved.more_tables == 3
