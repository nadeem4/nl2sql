"""The Phase 2 events, published (row E4 of docs/architecture/wave_a.md).

Phase 2 returned this data instead of publishing it (phase2.md, section 9);
Wave A publishes it. Engine events (an index build, a datasource check, a
purge) go through the runtime's bus, ``ctx.bus``, because they run outside
any question. Run events (a parse retry, a policy denial) go through the
run's scope. Events never carry sample values, rows or keys (D15), and
``policy.decision`` is published for denials at the three enforcement points
only (D16), never on the caller's stream (D17).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from index_helpers import TestIndex, memory_state  # same directory
from langchain_core.embeddings import DeterministicFakeEmbedding, FakeEmbeddings

from nl2sql import NL2SQL, Embeddings
from nl2sql.events import STREAM_EVENTS, EventBus, RunScope
from nl2sql.indexing.rebuild import rebuild_index
from nl2sql.policies import UserContext
from nl2sql.testing.config import make_config
from nl2sql_sdk import IndexScope
from nl2sql_sdk.schema import (ColumnContract, ColumnMetadata, ColumnStatistics, IndexIssue, SchemaContract,
                               SchemaMetadata, SchemaSnapshot, TableContract, TableMetadata, TableRef)

SAMPLE = "Zanzibar-sample-value"


def _capture(bus: EventBus, *names):
    seen = []
    bus.subscribe(seen.append, names=list(names) or None)
    return seen


# ---------------------------------------------------------------- index builds

def _snapshot(issues=()) -> SchemaSnapshot:
    ref = TableRef(schema_name="main", table_name="Artist")
    stats = ColumnStatistics(null_percentage=0.0, distinct_count=1, sample_values=[SAMPLE])
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="chinook", engine_type="sqlite", tables={ref.full_name: TableContract(
            table=ref, columns={"Name": ColumnContract(name="Name", data_type="TEXT")})}),
        metadata=SchemaMetadata(datasource_id="chinook", engine_type="sqlite", tables={ref.full_name: TableMetadata(
            table=ref, columns={"Name": ColumnMetadata(statistics=stats)}, row_count=1)}),
        issues=list(issues))


class _Adapter:
    datasource_id = "chinook"

    def __init__(self, snapshot=None, error=None):
        self.snapshot, self.error = snapshot or _snapshot(), error

    def fetch_schema_snapshot(self, scope, on_table=None):
        if self.error:
            raise self.error
        return self.snapshot


def _ctx(tmp_path, adapter):
    ctx = TestIndex(tmp_path, FakeEmbeddings(size=8), state=memory_state()).ctx
    ctx.ds_registry = SimpleNamespace(
        list_adapters=lambda: [adapter], unavailable=dict, get_index_scope=lambda _ds: IndexScope(),
        get_sample_questions=lambda _ds: [], get_description=lambda _ds: "Music store")
    return ctx


def test_index_build_publishes_started_and_swapped_on_ctx_bus(tmp_path):
    ctx = _ctx(tmp_path, _Adapter())
    seen = _capture(ctx.bus, "index.started", "index.swapped", "index.failed")

    assert rebuild_index(ctx).ok
    first = ctx.index.pointers(ctx.namespace)["chinook"]
    assert rebuild_index(ctx).ok
    second = ctx.index.pointers(ctx.namespace)["chinook"]

    assert [e.name for e in seen] == ["index.started", "index.swapped"] * 2
    started, swapped = seen[0], seen[3]
    assert started.data == {"datasource_id": "chinook", "namespace": ctx.namespace}
    assert started.trace_id is None and started.tenant_id == ctx.tenant_id
    assert swapped.data["build_id"] == second.build_id
    assert swapped.data["schema_version"] == second.schema_version
    assert swapped.data["replaced_build_id"] == first.build_id
    assert swapped.data["replaced_schema_version"] == first.schema_version
    assert swapped.data["namespace"] == ctx.namespace
    assert swapped.data["counts"] and all(isinstance(v, int) for v in swapped.data["counts"].values())
    assert seen[1].data["replaced_build_id"] is None


def test_index_build_failure_publishes_index_failed(tmp_path):
    ctx = _ctx(tmp_path, _Adapter(error=RuntimeError("database went away")))
    seen = _capture(ctx.bus, "index.started", "index.swapped", "index.failed")

    assert not rebuild_index(ctx).ok

    assert [e.name for e in seen] == ["index.started", "index.failed"]
    assert seen[1].data == {"datasource_id": "chinook", "reason": "database went away"}


def test_index_issue_publishes_column_failed(tmp_path):
    # A driver's message can quote a cell value (a cast error while sampling), so for the values and
    # profile stages only the error's type and a fixed description are published.
    issues = [IndexIssue(table="main.Artist", column="Name", stage="values",
                         message=f'invalid input syntax for type integer: "{SAMPLE}"', error_type="DataError"),
              IndexIssue(table="main.Artist", column="ArtistId", stage="profile", message=f"bad value {SAMPLE}"),
              IndexIssue(table="main.*", stage="estimate", message="no catalog row estimates")]
    ctx = _ctx(tmp_path, _Adapter(_snapshot(issues)))
    seen = _capture(ctx.bus)

    assert rebuild_index(ctx).ok

    failed = [e.data for e in seen if e.name == "index.column_failed"]
    assert failed == [
        {"datasource_id": "chinook", "table": "main.Artist", "column": "Name", "stage": "values",
         "message": "The column's values could not be read (DataError)."},
        {"datasource_id": "chinook", "table": "main.Artist", "column": "ArtistId", "stage": "profile",
         "message": "The column could not be profiled."},
        {"datasource_id": "chinook", "table": "main.*", "column": None, "stage": "estimate",
         "message": "no catalog row estimates"},
    ]
    # Never a sample value (D15): not on this event, nor any other of the build.
    assert SAMPLE not in json.dumps([e.model_dump(mode="json") for e in seen])


# ---------------------------------------------------------------- engine: datasources and purge

def _engine(tmp_path: Path, **sections) -> NL2SQL:
    cfg = make_config(tmp_path, **sections)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))


BROKEN = {"id": "broken", "type": "sqlite", "connection": {"database": "no/such/folder/x.db"}}


def test_failed_check_publishes_datasource_failed(tmp_path):
    from nl2sql import Datasource

    with _engine(tmp_path, datasources=[BROKEN]) as engine:
        seen = []
        engine.events.subscribe(seen.append, names=["datasource.failed"])
        status = engine.datasources.test("broken")
        tried = engine.datasources.test(Datasource(id="typo", type="sqlit", connection={"database": "x.db"}))

    assert [e.data["datasource_id"] for e in seen] == ["broken", "typo"]
    checked, typo = seen
    assert checked.data == {"datasource_id": "broken", "stage": status.stage, "message": status.reason,
                            "fix": status.fix}
    assert typo.data["stage"] == tried.stage == "config"
    assert checked.trace_id is None and checked.tenant_id == engine.config.settings.tenant_id


PASSWORD = "Hunter2SecretPw"
LEAKY = (f"could not connect to postgresql://admin:{PASSWORD}@db.internal:5432/sales; password={PASSWORD}; "
         f"retried admin:{PASSWORD}@db.internal")


def test_datasource_failed_redacts_a_password_in_a_dsn(tmp_path):
    from nl2sql import Datasource

    class Plug:
        datasource_id = "plug"

        def test_connection(self):
            raise RuntimeError(LEAKY)

    with _engine(tmp_path) as engine:
        seen = []
        engine.events.subscribe(seen.append, names=["datasource.failed"])
        engine.datasources.test(Datasource(id="plug", adapter=Plug()))

    [event] = seen
    assert PASSWORD not in event.model_dump_json()
    assert "[REDACTED]" in event.data["message"] and "db.internal" in event.data["message"]


def test_index_failed_redacts_a_password_in_a_dsn(tmp_path):
    ctx = _ctx(tmp_path, _Adapter(error=RuntimeError(LEAKY)))
    seen = _capture(ctx.bus, "index.failed")

    assert not rebuild_index(ctx).ok

    [event] = seen
    assert PASSWORD not in event.model_dump_json() and "[REDACTED]" in event.data["reason"]


def test_index_column_failed_redacts_a_password_in_a_dsn(tmp_path):
    issue = IndexIssue(table="main.Artist", stage="reflect", message=LEAKY)
    ctx = _ctx(tmp_path, _Adapter(_snapshot([issue])))
    seen = _capture(ctx.bus, "index.column_failed")

    assert rebuild_index(ctx).ok

    [event] = seen
    assert PASSWORD not in event.model_dump_json() and "[REDACTED]" in event.data["message"]


def test_a_ready_check_publishes_nothing(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE t (id INTEGER)")
    with _engine(tmp_path, datasources=[{"id": "shop", "type": "sqlite",
                                         "connection": {"database": "shop.db"}}]) as engine:
        seen = []
        engine.events.subscribe(seen.append, names=["datasource.failed"])
        assert engine.datasources.test("shop").status == "ready"
    assert seen == []


def test_purge_publishes_counts(tmp_path):
    with _engine(tmp_path, observers=[{"type": "audit"}]) as engine:
        seen = []
        engine.events.subscribe(seen.append, names=["retention.purged"])
        counts = engine.storage.purge()

    [event] = seen
    assert event.data == {"counts": counts}
    assert set(counts) == {"traces", "results", "audit"}
    assert event.trace_id is None
    # It is audited (D19): the audit observer wrote it.
    lines = [json.loads(line) for path in (engine.config.storage.path / "audit").glob("*.jsonl")
             for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["name"] for line in lines] == ["retention.purged"]


# ---------------------------------------------------------------- llm.parse_retry

def test_parse_retry_is_published_in_the_run():
    from langchain_core.prompts import ChatPromptTemplate
    from pydantic import BaseModel

    from nl2sql.llm import LLMRegistry
    from nl2sql.llm.models import AgentConfig
    from nl2sql.llm.wires import structured
    from nl2sql.secrets import SecretManager
    from nl2sql.testing.fake_llm import FakeLLMServer, Rule

    class Pick(BaseModel):
        table: str

    server = FakeLLMServer([Rule("Pick", {"table": "orders"}, when="did not match the schema"),
                            Rule("Pick", {"tabel": SAMPLE})]).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.register_llm(AgentConfig(provider="openai_compatible", model="m", base_url=server.base_url,
                                          structured_output="tool_calling", name="default"))
        chain = ChatPromptTemplate.from_template("Which table holds {q}?") | structured(
            registry.get_llm("default"), Pick)
        bus = EventBus()
        seen = _capture(bus, "llm.parse_retry")
        scope = RunScope(bus, question="q")
        with scope.entered():
            assert chain.invoke({"q": "orders"}, config={"metadata": {"langgraph_node": "ast_planner"}}) == Pick(
                table="orders")
        # Outside a run nothing is published.
        chain.invoke({"q": "orders"})
    finally:
        server.stop()

    [event] = seen
    assert event.trace_id == scope.trace_id
    assert event.data == {"agent": "ast_planner", "schema": "Pick", "attempt": 1,
                          "errors": ["`table`: Field required"]}
    assert SAMPLE not in event.model_dump_json()  # validation messages only, never the answer


# ---------------------------------------------------------------- policy.decision

def _validator_state(role):
    from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem
    from nl2sql.pipeline.nodes.ast_planner.schemas import TableRef as PlanTable
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
    from nl2sql.pipeline.state import SubgraphExecutionState

    plan = PlanModel(query_type="READ", tables=[PlanTable(name="secret", alias="s")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="s", column_name="id"))], joins=[])
    return SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="secret", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan), user_context=UserContext(roles=[role]))


def test_validator_denial_publishes_policy_decision():
    from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
    from nl2sql.testing.runtime import fake_runtime

    ctx = fake_runtime(ds_registry=SimpleNamespace(),
                       policies={"roles": {"user": {"description": "d", "allow": ["ds1.main.allowed"]}}})
    bus = EventBus()
    seen = _capture(bus, "policy.decision")
    scope = RunScope(bus, question="q")
    with scope.entered():
        LogicalValidatorNode(ctx)(_validator_state("user"))

    [event] = seen
    assert event.trace_id == scope.trace_id
    assert event.data["datasource_id"] == "ds1" and event.data["table"] == "secret"
    assert event.data["schema"] == "main" and event.data["step"] == "logical_validator"
    assert "user" in event.data["rule"]
    assert "policy.decision" not in STREAM_EVENTS  # an operator event (D17)


def test_a_self_join_on_a_denied_table_publishes_one_policy_decision():
    from nl2sql.pipeline.nodes.ast_planner.schemas import TableRef as PlanTable
    from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
    from nl2sql.testing.runtime import fake_runtime

    ctx = fake_runtime(ds_registry=SimpleNamespace(),
                       policies={"roles": {"user": {"description": "d", "allow": ["ds1.main.allowed"]}}})
    state = _validator_state("user")
    plan = state.ast_planner_response.plan
    state.ast_planner_response.plan = plan.model_copy(
        update={"tables": [*plan.tables, PlanTable(name="secret", alias="s2")]})
    bus = EventBus()
    seen = _capture(bus, "policy.decision")
    with RunScope(bus, question="q").entered():
        LogicalValidatorNode(ctx)(state)

    assert [(e.data["table"], e.data["step"]) for e in seen] == [("secret", "logical_validator")]


def test_retriever_filtering_publishes_nothing():
    """The schema retriever hides a denied table: filtering, not a refusal, so nothing is published (D16)."""
    from test_policy_refusal import _assert_customer_is_hidden, _retrieve, _retriever  # same directory

    bus = EventBus()
    seen = _capture(bus)
    with RunScope(bus, question="q").entered():
        _assert_customer_is_hidden(_retrieve(_retriever(), ["viewer"]))
    assert [e.name for e in seen if e.name == "policy.decision"] == []


def test_verified_query_refusal_publishes_policy_decision(db):
    from test_curated_matcher import CHOOSE_TOP, _run  # same directory

    bus = EventBus()
    seen = _capture(bus, "policy.decision")
    scope = RunScope(bus, question="q")
    with scope.entered():
        out, _, _ = _run(db, CHOOSE_TOP, role="viewer")

    assert out["curated_match"]["reason"] == "policy_denied"
    [event] = seen
    assert event.data["datasource_id"] == "shop" and event.data["step"] == "curated_matcher"
    assert event.data["table"] in {"Invoice", "InvoiceLine"} and event.data["rule"]


def _resolve(datasource_id=None):
    from nl2sql.llm import LLMRegistry
    from nl2sql.llm.models import AgentConfig
    from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
    from nl2sql.pipeline.state import GraphState
    from nl2sql.secrets import SecretManager
    from nl2sql.testing.runtime import fake_runtime

    # The role check refuses before the model is asked, so this model is never called.
    llm_registry = LLMRegistry(SecretManager())
    llm_registry.register_llm(AgentConfig(provider="openai", model="m", api_key="-".join(["fake", "key"]),
                                          base_url="http://127.0.0.1:9", name="default"))
    vector_store = SimpleNamespace(schema_version=lambda _id: "v1", snapshot=lambda _id: None)
    ctx = fake_runtime(vector_store=vector_store,
                       policies={"default_role": "r", "roles": {"r": {"description": "d", "allow": ["other"]}}},
                       llm_registry=llm_registry,
                       ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"},
                                                   list_ids=lambda: ["ds1", "ds2"]))
    bus = EventBus()
    seen = _capture(bus, "policy.decision")
    with RunScope(bus, question="q").entered():
        out = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext(roles=["r"]),
                                                     datasource_id=datasource_id))
    assert [e.error_code.value for e in out["errors"]] == ["SECURITY_VIOLATION"], out["errors"]
    return seen


def test_resolver_refusal_publishes_policy_decision():
    seen = _resolve()

    assert [e.data["datasource_id"] for e in seen] == ["ds1", "ds2"]  # every registered one was refused
    event = seen[0]
    assert event.data["table"] is None
    assert event.data["schema"] is None and event.data["step"] == "datasource_resolver"
    assert event.data["rule"] == "no allow pattern of role(s) 'r' reaches 'ds1'"


def test_explicit_datasource_refusal_publishes_policy_decision():
    seen = _resolve(datasource_id="ds1")

    assert [(e.data["datasource_id"], e.data["rule"]) for e in seen] == [
        ("ds1", "no allow pattern of role(s) 'r' reaches 'ds1'")]


def test_datasource_rule_names_the_roles_or_the_policy_check():
    from nl2sql.policies.scope import RoleGrant, Scope

    reaching = (RoleGrant.of("r", ["ds1"]),)
    assert Scope("ds1", reaching).datasource_rule() is None
    assert Scope("ds1", reaching).reaches_datasource()
    assert Scope("ds1", (RoleGrant.of("r", ["other"]),)).datasource_rule() == (
        "no allow pattern of role(s) 'r' reaches 'ds1'")
    failed = Scope("ds1", reaching, check=RoleGrant("policy check 'acl' (failed: RuntimeError)", ()))
    assert failed.datasource_rule() == "policy check 'acl' (failed: RuntimeError) does not reach 'ds1'"
    assert not failed.reaches_datasource()


@pytest.fixture
def db(tmp_path):
    """The curated matcher tests' shop database, smaller."""
    import sqlite3


    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, FirstName TEXT);
        CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, InvoiceDate TEXT);
        CREATE TABLE InvoiceLine (InvoiceLineId INTEGER PRIMARY KEY, InvoiceId INTEGER, UnitPrice REAL,
                                  Quantity INTEGER);
        INSERT INTO Customer VALUES (1, 'Ana');
        INSERT INTO Invoice VALUES (10, 1, '2012-02-01');
        INSERT INTO InvoiceLine VALUES (100, 10, 1.0, 3);
    """)
    conn.commit()
    conn.close()
    return path
