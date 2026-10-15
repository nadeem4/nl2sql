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
    fused = fuse({"description": ["a", "b"], "tables": ["b", "a"], "columns": ["b"]}, rrf_k=60,
                 datasource_ids=["a", "b"])

    assert [c.datasource_id for c in fused] == ["b", "a"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61 + 1 / 61)
    # a has no column entry: it ranks after every datasource the signal ranked, not nowhere.
    assert fused[1].score == pytest.approx(1 / 61 + 1 / 62 + 1 / 62)
    assert fused[0].ranks == {"description": 2, "tables": 1, "columns": 1}
    assert fused[1].ranks == {"description": 1, "tables": 2}


def test_having_entries_of_a_type_is_not_a_vote():
    """Review of PR 316: only chinook had verified queries, and that alone won it nearly every question."""
    fused = fuse({"description": ["b", "a", "c"], "examples": ["b", "c", "a"], "curated": ["a"],
                  "tables": ["b", "c", "a"], "columns": ["b", "a", "c"]}, rrf_k=60, datasource_ids=["a", "b", "c"])

    assert fused[0].datasource_id == "b"


def test_a_signal_with_no_entries_changes_nothing():
    with_empty = fuse({"description": ["a", "b"], "examples": []}, rrf_k=60, datasource_ids=["a", "b"])
    without = fuse({"description": ["a", "b"]}, rrf_k=60, datasource_ids=["a", "b"])

    assert [(c.datasource_id, c.score) for c in with_empty] == [(c.datasource_id, c.score) for c in without]


def test_verified_queries_on_one_datasource_do_not_outvote_every_other_signal(tmp_path):
    index = TestIndex(tmp_path, BagEmbeddings())
    index.write("a", [
        Chunk("a store", type="schema.datasource", datasource_id="a", id="ds:a"),
        Chunk("artist album", type="schema.table", datasource_id="a", table="[main].[Artist]", id="t:a"),
        Chunk("artist name", type="schema.column", datasource_id="a", table="[main].[Artist]", column="Name",
              id="c:a"),
        Chunk("best artist", type="curated.verified_query", datasource_id="a", query_id="q1", id="vq:a"),
    ])
    index.write("b", [
        Chunk("employee price", type="schema.datasource", datasource_id="b", id="ds:b"),
        Chunk("employee", type="schema.table", datasource_id="b", table="[main].[Employee]", id="t:b"),
        Chunk("employee price", type="schema.column", datasource_id="b", table="[main].[Employee]",
              column="Price", id="c:b"),
    ])
    index.write("c", [
        Chunk("employee sales", type="schema.datasource", datasource_id="c", id="ds:c"),
        Chunk("employee invoice", type="schema.table", datasource_id="c", table="[main].[Staff]", id="t:c"),
        Chunk("employee name", type="schema.column", datasource_id="c", table="[main].[Staff]",
              column="Name", id="c:c"),
    ])

    # Every signal but curated ranks b first and a last; only a has verified queries.
    routed = index.retriever().route("employee price", ["a", "b", "c"], floor_ratio=0.0)

    assert routed[0].datasource_id == "b"


def test_floor_ratio_drops_a_datasource_far_less_similar_than_the_best(tmp_path):
    """The floor is on similarity: RRF scores of a handful of datasources are always within a few percent."""
    index = TestIndex(tmp_path, BagEmbeddings())
    index.write("music", [Chunk("genre track album artist", type="schema.datasource", datasource_id="music",
                                id="ds:music")])
    index.write("sales", [Chunk("genre track sales", type="schema.datasource", datasource_id="sales",
                                id="ds:sales")])
    index.write("hr", [Chunk("employee", type="schema.datasource", datasource_id="hr", id="ds:hr")])
    retriever = index.retriever()
    explain: list = []

    kept = retriever.route("genre track album artist", ["music", "sales", "hr"], explain=explain, floor_ratio=0.5)

    assert [c.datasource_id for c in kept] == ["music", "sales"]
    routing = explain[-1]
    assert routing["dropped"] == [{"datasource_id": "hr", "reason": "floor"}]
    best = {f["datasource_id"]: f["similarity"] for f in routing["fused"]}
    assert best["hr"] < 0.5 * best["music"] <= best["sales"]
    # floor_ratio 0 keeps all of them; limit cuts what is left.
    kept = retriever.route("genre track album artist", ["music", "sales", "hr"], floor_ratio=0.0, limit=1)
    assert [c.datasource_id for c in kept] == ["music"]


def test_shortlist_cuts_on_similarity_then_limit():
    fused = fuse({"description": ["a", "b", "c"]}, rrf_k=60, datasource_ids=["a", "b", "c"],
                 similarity={"a": 0.8, "b": 0.5, "c": 0.3})

    kept, dropped = shortlist(fused, limit=1, floor_ratio=0.5)

    assert [c.datasource_id for c in kept] == ["a"]
    assert dropped == [{"datasource_id": "b", "reason": "limit"}, {"datasource_id": "c", "reason": "floor"}]
    # floor_ratio 0 keeps every candidate, even one with a negative similarity.
    fused = fuse({"description": ["a", "b"]}, rrf_k=60, datasource_ids=["a", "b"], similarity={"a": 0.4, "b": -0.1})
    assert [c.datasource_id for c in shortlist(fused, limit=5, floor_ratio=0.0)[0]] == ["a", "b"]


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


def _scoped_index(tmp_path) -> TestIndex:
    """support has a readable tickets table and a denied agents table; one verified query reads agents."""
    index = TestIndex(tmp_path, BagEmbeddings())
    index.write("support", [
        Chunk("customer", type="schema.datasource", datasource_id="support", id="ds:support"),
        Chunk("customer invoice", type="schema.table", datasource_id="support", table="[main].[tickets]",
              id="t:support:tickets"),
        Chunk("employee name price", type="schema.table", datasource_id="support", table="[main].[agents]",
              id="t:support:agents"),
        Chunk("employee name", type="schema.column", datasource_id="support", table="[main].[agents]",
              column="email", id="c:support:agents.email"),
        Chunk("employee price", type="curated.verified_query", datasource_id="support", query_id="agent_pay",
              id="vq:support:agent_pay"),
    ])
    index.write("hr", [
        Chunk("staff", type="schema.datasource", datasource_id="hr", id="ds:hr"),
        Chunk("album", type="schema.table", datasource_id="hr", table="[main].[agents]", id="t:hr:agents"),
    ])
    return index


def _hides_agents(metadata) -> bool:
    """support's agents table and the verified query that reads it are denied; hr's agents table is not."""
    if metadata.get("type") == "curated.verified_query":
        return metadata.get("query_id") != "agent_pay"
    return not (metadata.get("datasource_id") == "support" and metadata.get("table") == "[main].[agents]")


def test_denied_table_never_votes_or_appears_in_the_trace(tmp_path):
    retriever = _scoped_index(tmp_path).retriever()
    explain: list = []

    routed = retriever.route("employee name price", ["support", "hr"], explain=explain, floor_ratio=0.0,
                             allows=_hides_agents)

    text = str(explain)
    assert "t:support:agents" not in text and "c:support:agents.email" not in text and "agent_pay" not in text
    assert "t:hr:agents" in text  # the same table name in another datasource is not denied
    by_id = {c.datasource_id: c for c in routed}
    assert "tables" in by_id["support"].ranks  # tickets, its one visible table, still votes
    assert "columns" not in by_id["support"].ranks and "curated" not in by_id["support"].ranks


def test_vote_k_counts_only_visible_entries(tmp_path, monkeypatch):
    index = TestIndex(tmp_path, BagEmbeddings())
    denied = [Chunk("employee", type="schema.table", datasource_id="a", table=f"[main].[D{i}]", id=f"t:a:D{i}")
              for i in range(6)]
    index.write("a", [Chunk("x", type="schema.datasource", datasource_id="a", id="ds:a"), *denied,
                      Chunk("employee price", type="schema.table", datasource_id="a", table="[main].[Ok]",
                            id="t:a:Ok")])
    retriever = index.retriever()
    monkeypatch.setattr(Retriever, "VOTE_K", 1)
    explain: list = []

    retriever.route("employee", ["a"], explain=explain,
                    allows=lambda md: not str(md.get("table", "")).startswith("[main].[D"))

    [tables] = [r for r in explain if r["search"] == "route.tables"]
    assert [e["id"] for e in tables["pool"]] == ["t:a:Ok"]


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
    # A pool, so the playground draws the fused ranking like any other search.
    assert [e["datasource_id"] for e in routing["pool"]] == [f["datasource_id"] for f in routing["fused"]]
    assert routing["pool"][0]["picked"] is True and routing["pool"][0]["rank"] == 1


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

    def route(self, question, datasource_ids, explain=None, *, limit=3, floor_ratio=0.5, allows=None):
        self.calls.append({"datasource_ids": list(datasource_ids), "limit": limit, "floor_ratio": floor_ratio,
                           "allows": allows})
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
    [call] = store.calls
    assert (call["datasource_ids"], call["limit"], call["floor_ratio"]) == (["a", "b", "c", "d"], 4, 0.1)


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


def test_resolver_routes_only_over_the_tables_and_verified_queries_the_scope_allows(judge):
    """A denied table, and a verified query that reads one, never vote (review of PR 316)."""
    from nl2sql.config.curated import CuratedDatasource
    from nl2sql.config.models import Curated

    store = RouteStore(["a", "b"])
    curated = Curated(datasources={"a": CuratedDatasource(verified_queries=[
        {"id": "open_q", "description": "d", "questions": ["q"], "sql": "SELECT * FROM main.T0"},
        {"id": "denied_q", "description": "d", "questions": ["q"], "sql": "SELECT * FROM main.T0 JOIN main.T1 ON 1 = 1"},
        {"id": "broken_q", "description": "d", "questions": ["q"], "sql": "SELECT FROM WHERE"},
    ])})
    ctx = fake_runtime(
        vector_store=store, curated=curated, llm_registry=judge(["a"]),
        policies={"default_role": "r", "roles": {"r": {"description": "d", "allow": ["a", "b"],
                                                       "deny": ["a.main.T1"]}}},
        ds_registry=SimpleNamespace(list_ids=lambda: ["a", "b"], get_dialect=lambda _ds: "sqlite"))

    DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))

    allows = store.calls[0]["allows"]
    assert allows({"type": "schema.table", "datasource_id": "a", "table": "[main].[T0]"})
    assert not allows({"type": "schema.table", "datasource_id": "a", "table": "[main].[T1]"})
    assert not allows({"type": "schema.column", "datasource_id": "a", "table": "[main].[T1]", "column": "x"})
    assert allows({"type": "schema.table", "datasource_id": "b", "table": "[main].[T1]"})  # b's T1 is not denied
    assert not allows({"type": "schema.table", "datasource_id": "a", "table": "[main].[Unknown]"})
    assert allows({"type": "schema.datasource", "datasource_id": "a"})
    assert allows({"type": "curated.verified_query", "datasource_id": "a", "query_id": "open_q"})
    assert not allows({"type": "curated.verified_query", "datasource_id": "a", "query_id": "denied_q"})
    assert not allows({"type": "curated.verified_query", "datasource_id": "a", "query_id": "broken_q"})



def test_routing_keeps_three_candidates_by_default():
    """D28: the top 3 by fused score go to the answerability check."""
    from nl2sql.config.models import Settings

    assert Settings().routing.candidates == 3
