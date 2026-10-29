"""Table recall on a schema past the full-snapshot threshold (C-W4, docs/architecture/wave_c.md section 17).

The 0.9.0 smoke test asked Pagila "top 5 films by revenue in the top category".
"Revenue" is not near ``payment``, so vector search never sent it and the
planner asked a question instead of summing ``payment.amount``. The retriever
now adds the glossary's tables, the tables a model picks from a catalog, and
the tables that join them from the foreign-key graph. The model here is a fake
chain; a real key is never used.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda
from pagila_snapshot import DS, pagila_snapshot, ref

from nl2sql.config.models import AgentName
from nl2sql.knowledge.merge import LoadedSource, merge
from nl2sql.limits import LimitReached, RunStop
from nl2sql.llm.providers import LLM_AGENTS
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever import selector
from nl2sql.pipeline.nodes.schema_retriever.join_path import connect
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.knowledge import Glossary

QUESTION = "top 5 films by revenue in the top category"


def name(table: str) -> str:
    return ref(table).full_name


def _doc(table, **extra):
    return SimpleNamespace(metadata={"type": "schema.table", "table": name(table), **extra}, page_content="")


class Model:
    """The registry's model and the chain over it: records the prompt, answers with ``picks`` (or raises)."""

    def __init__(self, picks=(), raises=None):
        self.picks, self.raises, self.prompts, self.agents = list(picks), raises, [], []
        self.registry = SimpleNamespace(get_llm=self._get)

    def _get(self, agent):
        self.agents.append(agent)
        return object()

    def install(self, monkeypatch):
        def structured(_llm, schema):
            def answer(prompt):
                self.prompts.append(prompt.to_string())
                if self.raises:
                    raise self.raises
                return schema(tables=[n if n.startswith("[") else name(n) for n in self.picks])
            return RunnableLambda(answer)
        monkeypatch.setattr(selector, "structured", structured)
        return self


def _store(snapshot, vector=("film", "film_category", "category"), planning=()):
    asked = []

    def schema_context(_q, _ds, k=8, **_kw):
        asked.append(k)
        return [_doc(t) for t in vector][:k]

    return SimpleNamespace(
        snapshot=lambda _id: snapshot, retrieve_schema_context=schema_context, asked=asked,
        retrieve_column_candidates=lambda *_a, **_k: [],
        retrieve_planning_context=lambda *_a, **_k: [
            SimpleNamespace(metadata={"type": "schema.column", "table": name(t), "column": c}, page_content="")
            for t, c in planning])


def _node(store, model=None, settings=None, allow=("*",), knowledge=None, snapshot=None):
    return SchemaRetrieverNode(fake_runtime(
        settings={"retrieval": settings or {}}, vector_store=store, knowledge=knowledge,
        state=SimpleNamespace(get_snapshot=lambda _id, _v: snapshot),
        llm_registry=model.registry if model else None,
        policies={"roles": {"analyst": {"description": "d", "allow": list(allow)}}}))


def _state(question=QUESTION, **fields):
    return SubgraphExecutionState(
        trace_id="t", user_query=question, user_context=UserContext(roles=["analyst"]),
        sub_query=SubQuery(id="sq1", datasource_id=DS, intent=question), **fields)


def _tables(out):
    return {t.name: {c.name for c in t.columns} for t in out["relevant_tables"]}


def _steps(out):
    return {t["table"]: t["steps"] for t in out["retrieval"]["tables"]}


SIX = {"film", "film_category", "category", "inventory", "rental", "payment"}


def test_the_smoke_question_sends_every_table_it_needs(monkeypatch):
    model = Model(picks=["payment", "film"]).install(monkeypatch)

    out = _node(_store(pagila_snapshot()), model)(_state())

    tables = _tables(out)
    assert SIX <= set(tables)
    assert "amount" in tables["payment"]
    assert not out.get("warnings") and not out.get("errors")


def test_the_trace_says_which_step_added_each_table(monkeypatch):
    model = Model(picks=["payment", "film"]).install(monkeypatch)

    out = _node(_store(pagila_snapshot()), model)(_state())

    steps = _steps(out)
    assert steps["film"] == ["vector", "selector"]
    assert steps["category"] == ["vector"]
    assert steps["payment"] == ["selector"]
    assert steps["rental"] == steps["inventory"] == ["join_path"]
    # The record keeps the keys retrieval_recall.sent() reads.
    assert all({"table", "columns"} <= set(t) for t in out["retrieval"]["tables"])


def test_the_join_path_alone_connects_film_to_payment(monkeypatch):
    out = _node(_store(pagila_snapshot(), vector=("film", "payment")), settings={"table_selector": False})(_state())

    assert set(_tables(out)) == {"film", "payment", "inventory", "rental"}


def test_a_bridge_table_keeps_its_keys_not_all_its_columns():
    out = _node(_store(pagila_snapshot(), vector=("film", "payment")), settings={"table_selector": False})(_state())

    # inventory: its key, and both sides of the joins offered (film, store, and rental's key into it).
    assert _tables(out)["inventory"] == {"inventory_id", "film_id", "store_id"}


def test_a_path_longer_than_the_hop_limit_is_not_added():
    out = _node(_store(pagila_snapshot(), vector=("film", "payment")),
                settings={"table_selector": False, "join_path_max_hops": 2})(_state())

    assert set(_tables(out)) == {"film", "payment"}


def test_a_table_with_no_foreign_key_does_not_stop_the_others_connecting():
    seeds = ["film", "payment", "actor_info"]
    adjacency = _adjacency()

    added = connect(adjacency, [name(t) for t in seeds], max_hops=4)

    assert set(added) == {name("inventory"), name("rental")}


def _adjacency():
    snapshot = pagila_snapshot()
    adjacency = {key: set() for key in snapshot.contract.tables}
    for key, contract in snapshot.contract.tables.items():
        for fk in contract.foreign_keys:
            adjacency[key].add(fk.referred_table.full_name)
            adjacency[fk.referred_table.full_name].add(key)
    return adjacency


def test_selector_picks_keep_all_their_columns_up_to_forty(monkeypatch):
    model = Model(picks=["payment", "film"]).install(monkeypatch)

    out = _node(_store(pagila_snapshot(), planning=[("film", "rating")]), model)(_state())

    tables = _tables(out)
    assert tables["payment"] == {"payment_id", "customer_id", "staff_id", "rental_id", "amount", "payment_date"}
    assert "description" in tables["film"]  # the selector picked it too: all its columns


def test_a_selector_pick_past_forty_columns_gets_the_search_and_the_keys(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)
    snapshot = pagila_snapshot(extra_columns={"payment": 45})

    out = _node(_store(snapshot, planning=[("payment", "amount")]), model)(_state())

    columns = _tables(out)["payment"]
    assert columns == {"amount", "payment_id", "customer_id", "staff_id", "rental_id"}


def test_a_hidden_table_never_reaches_the_catalog_the_result_or_a_path(monkeypatch):
    model = Model(picks=["payment", "film"]).install(monkeypatch)
    allow = [f"{DS}.public.{t}" for t in ("film", "film_category", "category", "inventory", "rental", "payment",
                                          "customer", "staff", "actor", "language", "city", "country", "address",
                                          "film_actor", "actor_info", "customer_list", "film_list")]

    out = _node(_store(pagila_snapshot()), model, allow=allow)(_state())

    assert name("store") not in model.prompts[0] and name("store") not in str(out)
    assert name("store") not in str(out["retrieval"])
    assert SIX <= set(_tables(out))


def test_a_selector_that_raises_still_returns_the_other_steps_tables(monkeypatch, caplog):
    model = Model(raises=RuntimeError("provider down")).install(monkeypatch)
    store = _store(pagila_snapshot(), vector=("film", "payment"))

    with caplog.at_level(logging.WARNING):
        out = _node(store, model)(_state())

    assert set(_tables(out)) == {"film", "payment", "inventory", "rental"}
    assert not out.get("warnings") and not out.get("errors")
    assert "provider down" in caplog.text


def test_a_name_outside_the_catalog_is_dropped(monkeypatch):
    model = Model(picks=["payment", "[public].[nonexistent]", "film"]).install(monkeypatch)

    out = _node(_store(pagila_snapshot()), model)(_state())

    assert "nonexistent" not in str(out)
    assert SIX <= set(_tables(out))


def test_an_empty_pick_is_allowed(monkeypatch):
    model = Model(picks=[]).install(monkeypatch)

    out = _node(_store(pagila_snapshot()), model)(_state())

    assert set(_tables(out)) == {"film", "film_category", "category"}


def test_a_term_brings_its_tables_with_no_selector_call():
    glossary = Glossary.model_validate({"version": 1, "datasources": {DS: {"terms": [
        {"term": "revenue", "definition": "Sum of payment.amount.", "tables": ["payment"],
         "columns": ["payment.amount"]}]}}})
    knowledge = merge([LoadedSource(type="yaml", glossary=glossary, sha256=None, path=None,
                                    loaded_at=datetime.now(timezone.utc))])
    model = Model()

    out = _node(_store(pagila_snapshot()), model, settings={"table_selector": False}, knowledge=knowledge)(_state())

    assert SIX <= set(_tables(out))
    assert "amount" in _tables(out)["payment"]
    assert _steps(out)["payment"] == ["glossary"]
    assert model.agents == [] and model.prompts == []
    # Matched once, and the planner still gets the term.
    assert [t.term for t in out["knowledge_used"].terms] == ["revenue"]


def test_the_prompt_carries_the_catalog_the_question_and_what_is_already_chosen(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)
    snapshot = pagila_snapshot(comments={"payment": "x" * 300 + "TAIL"})

    _node(_store(snapshot), model)(_state())

    [prompt] = model.prompts
    assert QUESTION in prompt and "[TABLES]" in prompt
    assert name("rental") in prompt and "rental_date" in prompt
    assert "TAIL" not in prompt and "x" * 200 in prompt and "x" * 201 not in prompt
    assert name("film_category") in prompt[prompt.index("[ALREADY_SELECTED]"):]


def test_past_the_catalog_cap_the_catalog_is_the_nearest_tables(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)
    store = _store(pagila_snapshot(), vector=("film", "category", "customer", "rental"))

    _node(store, model, settings={"selector_catalog_max": 3})(_state())

    [prompt] = model.prompts
    catalog = prompt[prompt.rindex("[TABLES]"):prompt.rindex("[BUSINESS_CONTEXT]")]
    assert name("customer") in catalog and name("film") in catalog
    assert name("rental") not in catalog and name("staff") not in catalog
    assert 3 in store.asked


def test_at_or_below_the_threshold_no_selector_call_is_made(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)

    out = _node(_store(pagila_snapshot()), model, settings={"full_snapshot_max_tables": 100})(_state())

    assert len(out["relevant_tables"]) == 22
    assert model.agents == [] and model.prompts == []


def test_a_pinned_plan_sends_every_table_and_makes_no_selector_call(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)
    state = _state(pinned_plan=PlanModel(tables=[], select_items=[]))

    out = _node(_store(pagila_snapshot()), model)(state)

    assert len(out["relevant_tables"]) == 22
    assert model.agents == []


def test_the_limit_reached_by_the_selector_ends_the_run(monkeypatch):
    model = Model(raises=LimitReached(RunStop(limit="max_llm_calls", value=1, spent=1))).install(monkeypatch)

    with pytest.raises(LimitReached):
        _node(_store(pagila_snapshot()), model)(_state())


def test_with_the_selector_off_and_no_model_configured_the_node_retrieves():
    out = _node(_store(pagila_snapshot(), vector=("film", "payment")), settings={"table_selector": False})(_state())

    assert {"film", "payment"} <= set(_tables(out))


def test_the_model_is_built_on_the_first_call_and_not_before(monkeypatch):
    model = Model(picks=["payment"]).install(monkeypatch)
    node = _node(_store(pagila_snapshot()), model)

    assert model.agents == []
    node(_state())
    node(_state())

    assert model.agents == ["tableselector"]


def test_the_selector_is_a_named_agent_with_settings():
    from nl2sql.config import Settings

    assert "table_selector" in AgentName.__args__
    assert LLM_AGENTS["table_selector"] == "tableselector"
    retrieval = Settings().retrieval
    assert (retrieval.table_selector, retrieval.selector_catalog_max, retrieval.join_path_max_hops) == (True, 200, 4)
