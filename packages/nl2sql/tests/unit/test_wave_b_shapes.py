"""The shapes Wave B's tracks share, added before any of them (docs/architecture/wave_b.md, D59).

The SDK's glossary models, ``KnowledgeSource``, ``Proposal``, ``Estimate`` and
``Overlap``; the state store's proposal methods; the config sections and
settings; the registry kind; the error codes; the result types; the pipeline
state fields; the events; and the stub ``engine.knowledge`` and
``engine.proposals`` namespaces and CLI groups.
"""
from __future__ import annotations

import inspect
import sqlite3
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pytest
import typer.main
import yaml
from pydantic import ValidationError

from nl2sql import Config, ConfigError, QueryResult, Registry, Settings, UserContext, load_config
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.config import CostRule, KnowledgeSourceConfig, Policies, Role
from nl2sql.config.models import AgentName
from nl2sql.registry import GROUPS, INTERFACE_VERSIONS
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.config import make_config
from nl2sql_sdk import (
    DatasourceCapability,
    Estimate,
    EstimatingAdapterProtocol,
    Overlap,
    ProfilingAdapterProtocol,
)
from nl2sql_sdk.knowledge import ColumnKnowledge, Glossary, JoinKnowledge, KnowledgeSource, Term
from nl2sql_sdk.stores import BuildPointer, Proposal

# ---------------------------------------------------------------- the glossary (SDK)

GLOSSARY = """
version: 1
datasources:
  chinook:
    tables:
      Invoice: {description: One row per customer invoice. Total includes tax.}
      InvoiceLine: {description: One row per track sold on an invoice., notes: Refunds are not recorded anywhere.}
    columns:
      Invoice.Total: {description: Invoice total in US dollars, synonyms: [invoice amount], role: money, unit: USD}
      Invoice.InvoiceDate: {grain: day}
      Track.MediaTypeId:
        role: enum
        values: {"1": MPEG audio, "2": Protected AAC audio}
    joins:
      - {from: Track.MediaTypeId, to: MediaType.MediaTypeId, cardinality: many-to-one, notes: Declared nowhere.}
    terms:
      - {term: revenue, synonyms: [sales], definition: Sum of unit price times quantity.,
         columns: [InvoiceLine.UnitPrice, InvoiceLine.Quantity]}
    rules:
      - {id: no_test_customers, text: Leave out customers whose email ends in @example.com., tables: [Customer]}
    examples:
      - Which genres sell best in Germany?
"""


def test_the_design_glossary_loads():
    glossary = Glossary.model_validate(yaml.safe_load(GLOSSARY))
    chinook = glossary.datasources["chinook"]
    assert chinook.columns["Track.MediaTypeId"].values == {"1": "MPEG audio", "2": "Protected AAC audio"}
    join = chinook.joins[0]
    assert (join.from_, join.to, join.cardinality) == ("Track.MediaTypeId", "MediaType.MediaTypeId", "many-to-one")
    assert join.model_dump(by_alias=True, exclude_defaults=True)["from"] == "Track.MediaTypeId"
    assert chinook.terms[0].columns == ["InvoiceLine.UnitPrice", "InvoiceLine.Quantity"]
    assert JoinKnowledge(**{"from": ["A.x", "A.y"], "to": ["B.x", "B.y"]}).cardinality == "unknown"


@pytest.mark.parametrize("data", [
    {"version": 2, "datasources": {}},
    {"datasources": {}},
    {"version": 1, "datasources": {"d": {"tables": {"T": {"descripton": "typo"}}}}},
    {"version": 1, "datasources": {"d": {"columns": {"T.c": {"role": "currency"}}}}},
    {"version": 1, "datasources": {"d": {"columns": {"T.c": {"grain": "hour"}}}}},
    {"version": 1, "datasources": {"d": {"rules": [{"id": "Not A Slug", "text": "x"}]}}},
    {"version": 1, "datasources": {"d": {"rules": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}}},
    {"version": 1, "datasources": {"d": {"columns": {"T.c": {"role": "money", "values": {"1": "one"}}}}}},
    {"version": 1, "datasources": {"d": {"joins": [{"from": "A.x", "to": "B.x", "cardinality": "some"}]}}},
])
def test_glossary_is_strict_and_versioned(data):
    with pytest.raises(ValidationError):
        Glossary.model_validate(data)


def test_glossary_enum_codes_are_strings():
    """A code is written as a string: YAML reads an unquoted 1 as a number, which is refused, not coerced."""
    with pytest.raises(ValidationError):
        Glossary.model_validate(yaml.safe_load("version: 1\ndatasources: {d: {columns: {T.c: "
                                               "{role: enum, values: {1: MPEG}}}}}"))
    assert ColumnKnowledge(role="enum", values={"1": "MPEG"}).values == {"1": "MPEG"}


def test_long_glossary_text_is_a_load_error():
    """Nothing is silently cut: a text over settings.knowledge.item_max_chars (500) names the item."""
    long = "x" * 501
    with pytest.raises(ValidationError) as caught:
        Glossary.model_validate({"version": 1, "datasources": {"d": {"terms": [{"term": "t", "definition": long}]}}})
    assert "terms" in str(caught.value) and "500" in str(caught.value)
    Glossary.model_validate({"version": 1, "datasources": {"d": {"terms": [{"term": "t", "definition": long}]}}},
                            context={"item_max_chars": 600})
    with pytest.raises(ValidationError):
        Term.model_validate({"term": "t", "definition": "x" * 11}, context={"item_max_chars": 10})


def test_a_knowledge_source_is_a_protocol_a_plugin_satisfies():
    class Wiki:
        interface_version = 1
        config_model = KnowledgeSourceConfig

        @classmethod
        def from_config(cls, cfg, *, folder):
            return cls()

        def load(self):
            return Glossary(version=1, datasources={})

    assert isinstance(Wiki(), KnowledgeSource)
    assert Wiki().load().datasources == {}


# ---------------------------------------------------------------- proposals (SDK) and the state store

def _proposal(**overrides) -> Proposal:
    fields = dict(kind="join", datasource_id="chinook", target="Track.MediaTypeId->MediaType.MediaTypeId",
                  change={"from": "Track.MediaTypeId", "to": "MediaType.MediaTypeId", "cardinality": "many-to-one"},
                  tables=["chinook.main.Track", "chinook.main.MediaType"], origin="profiler",
                  evidence={"sampled": 347, "matched": 347, "overlap": 1.0}, schema_version="v1")
    fields.update(overrides)
    return Proposal.build(**fields)


def test_proposal_id_hashes_the_change():
    one = _proposal()
    assert len(one.id) == 16 and int(one.id, 16) >= 0
    assert one.status == "proposed" and one.created_at.tzinfo is not None
    assert _proposal(evidence={"sampled": 1}).id == one.id, "evidence is not part of the identity"
    assert _proposal(origin="draft").id == one.id, "nor is the origin"
    assert _proposal(change={**one.change, "cardinality": "unknown"}).id != one.id
    assert _proposal(target="other").id != one.id
    assert _proposal(kind="column").id != one.id
    assert _proposal(datasource_id="other").id != one.id
    reordered = dict(reversed(list(one.change.items())))
    assert _proposal(change=reordered).id == one.id, "the change is hashed as canonical JSON"
    with pytest.raises(ValidationError):
        _proposal(kind="metric")


@pytest.fixture
def store():
    state = SqliteState(":memory:")
    state.migrate()
    yield state
    state.close()


def test_a_0_7_state_file_upgrades_to_migration_5_keeping_its_rows(tmp_path):
    """A state.db that ran migrations 1 to 4 (release 0.7) gains only the proposals table."""
    from nl2sql.storage.sqlite import MIGRATIONS

    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE nl2sql_meta (migration TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);")
        for name, migration in MIGRATIONS[:4]:
            migration(connection)
            connection.execute("INSERT INTO nl2sql_meta VALUES (?, 0);", (name,))
        assert [name for name, _ in MIGRATIONS[:4]][-1] == "0004_schema_diffs"
        connection.execute("INSERT INTO plan_cache VALUES ('q', 'ds', 'v1', '{}', 0);")

    state = SqliteState(path)
    assert state.migrate() == ["0005_proposals"]
    assert state.get_plan("q", "ds", "v1") == "{}", "0.7's rows are kept"
    state.put_proposal(_proposal())
    assert state.get_proposal(_proposal().id) is not None
    state.close()


def test_migration_5_adds_the_proposals_table(tmp_path):
    state = SqliteState(tmp_path / "state.db")
    assert "0005_proposals" in state.migrate()
    assert state.migrate() == []
    state.close()
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'proposals'").fetchone()


def test_proposals_round_trip_through_the_state_store(store):
    join = _proposal()
    column = _proposal(kind="column", target="Invoice.Total", change={"role": "money"}, tables=["chinook.main.Invoice"],
                       created_at=join.created_at + timedelta(seconds=1))
    other = _proposal(datasource_id="hr", target="x", created_at=join.created_at + timedelta(seconds=2))
    for proposal in (join, column, other):
        store.put_proposal(proposal)

    assert store.get_proposal(join.id) == join
    assert store.get_proposal("0" * 16) is None
    assert [p.id for p in store.list_proposals()] == [other.id, column.id, join.id], "newest first"
    assert [p.id for p in store.list_proposals(kind="column")] == [column.id]
    assert [p.id for p in store.list_proposals(datasource_id="chinook")] == [column.id, join.id]
    assert [p.id for p in store.list_proposals(limit=1)] == [other.id]

    store.put_proposal(join.model_copy(update={"evidence": {"sampled": 10}}))
    assert store.get_proposal(join.id).evidence == {"sampled": 10}, "putting an id again updates its row"
    assert len(store.list_proposals()) == 3

    admin = "ada"
    decided = store.decide_proposal(join.id, "accepted", by=admin, wrote="glossary.yaml")
    assert (decided.status, decided.decided_by, decided.wrote) == ("accepted", "ada", "glossary.yaml")
    assert decided.decided_at is not None and store.get_proposal(join.id) == decided
    assert store.decide_proposal("0" * 16, "rejected", by=admin) is None
    assert [p.id for p in store.list_proposals(status="proposed")] == [other.id, column.id]


def test_decided_proposals_older_than_the_cutoff_are_purged(store):
    old, open_one = _proposal(), _proposal(target="open")
    store.put_proposal(old)
    store.put_proposal(open_one)
    store.decide_proposal(old.id, "rejected", by="ada", reason="wrong",
                          at=datetime.now(timezone.utc) - timedelta(days=400))

    assert store.purge_proposals(datetime.now(timezone.utc) - timedelta(days=365)) == 1
    assert store.get_proposal(old.id) is None
    assert store.get_proposal(open_one.id) is not None, "a proposed row is never purged"


def test_the_build_pointer_records_the_knowledge_hash():
    pointer = BuildPointer(build_id="b", schema_version="v", built_at=datetime.now(timezone.utc))
    assert pointer.knowledge_hash is None
    assert BuildPointer(**{**pointer.model_dump(), "knowledge_hash": "abc"}).knowledge_hash == "abc"


def test_every_built_in_store_is_interface_3():
    assert INTERFACE_VERSIONS["store"] == 3
    for name in ("chroma", "sqlite", "files", "local"):
        assert Registry.default().load("store", name).interface_version == 3


# ---------------------------------------------------------------- the adapter's optional methods (SDK)

def test_estimate_and_overlap_shapes():
    assert Estimate(source="EXPLAIN").rows is None
    assert Estimate(rows=12, cost=3.5, source="EXPLAIN").rows == 12
    assert Overlap(sampled=10, matched=7).matched == 7
    assert DatasourceCapability("estimates_cost") is DatasourceCapability.ESTIMATES_COST


def test_estimate_and_probe_overlap_are_optional_protocols():
    class Both:
        def estimate(self, sql, parameters):
            return Estimate(rows=1, source="test")

        def probe_overlap(self, left, right, sample_rows):
            return Overlap(sampled=0, matched=0)

    assert isinstance(Both(), EstimatingAdapterProtocol) and isinstance(Both(), ProfilingAdapterProtocol)
    assert not isinstance(object(), EstimatingAdapterProtocol)
    assert not isinstance(object(), ProfilingAdapterProtocol)


# ---------------------------------------------------------------- config

def _write(folder: Path, name: str, body) -> None:
    (folder / name).write_text(yaml.safe_dump(body), encoding="utf-8")


def _project(tmp_path: Path, **sections) -> Path:
    base = {
        "version": 2,
        "datasources": [{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
        "llm": {"provider": "openai", "model": "gpt-4o"},
        "embeddings": {"provider": "local"},
        "policies": {"allow_all": True},
        "storage": {"path": ".nl2sql"},
        **sections,
    }
    _write(tmp_path, "nl2sql.yaml", base)
    return tmp_path / "nl2sql.yaml"


def test_knowledge_section_is_a_list_of_sources(tmp_path):
    assert load_config(_project(tmp_path)).knowledge == [], "no section: no knowledge"
    _write(tmp_path, "knowledge.yaml", [{"type": "yaml", "path": "glossary.yaml"},
                                        {"type": "corp_catalog", "url": "${env:CATALOG_URL}"}])
    config = load_config(_project(tmp_path, knowledge={"file": "knowledge.yaml"}))

    first, second = config.knowledge
    assert isinstance(first, KnowledgeSourceConfig) and first.type == "yaml"
    assert first.model_extra == {"path": "glossary.yaml"}
    assert second.model_extra == {"url": "${env:CATALOG_URL}"}, "a plugin's references are kept until it is built"
    assert config.locate("knowledge[1].url") == ("knowledge.yaml", "[1].url")

    with pytest.raises(ConfigError) as caught:
        load_config(_project(tmp_path, knowledge=[{"path": "glossary.yaml"}]))
    assert "knowledge[0].type" in str(caught.value)


def test_a_knowledge_source_object_is_used_as_given(tmp_path):
    class Mine:
        def load(self):
            return Glossary(version=1, datasources={})

    mine = Mine()
    base = make_config(tmp_path)
    config = Config(**{name: getattr(base, name) for name in ("datasources", "llm", "embeddings", "policies",
                                                               "storage")},
                    knowledge=[KnowledgeSourceConfig(type="yaml", path="glossary.yaml"), mine])
    assert config.knowledge[1] is mine
    with pytest.raises(ValidationError):
        Config(**{name: getattr(base, name) for name in ("datasources", "llm", "embeddings", "policies", "storage")},
               knowledge=["glossary.yaml"])


def test_cost_rule_defaults_and_overrides():
    rule = CostRule()
    assert (rule.max_estimated_rows, rule.over_limit, rule.unknown) == (None, "confirm", "allow")
    policies = Policies(cost={"max_estimated_rows": 5_000_000},
                        roles={"analyst": {"description": "a", "cost": {"max_estimated_rows": 1_000_000,
                                                                         "over_limit": "refuse"}}})
    assert policies.cost.max_estimated_rows == 5_000_000
    assert policies.roles["analyst"].cost.model_fields_set == {"max_estimated_rows", "over_limit"}, \
        "a role replaces only the fields it sets"
    assert Policies().cost is None, "no cost block: no estimate is ever asked for"


@pytest.mark.parametrize("cost", [{"over_limit": "warn"}, {"unknown": "deny"}, {"max_estimated_rows": 0},
                                  {"max_estimated_bytes": 10}])
def test_cost_rule_actions_are_checked(cost):
    with pytest.raises(ValidationError):
        CostRule.model_validate(cost)
    with pytest.raises(ValidationError):
        Role(description="r", cost=cost)


def test_cost_rule_is_valid_beside_allow_all(tmp_path):
    config = load_config(_project(tmp_path, policies={"allow_all": True,
                                                      "cost": {"max_estimated_rows": 10, "over_limit": "refuse"}}))
    assert config.policies.allow_all and config.policies.cost.over_limit == "refuse"


def test_new_settings_have_the_designed_defaults():
    s = Settings()
    assert (s.knowledge.item_max_chars, s.knowledge.block_max_chars, s.knowledge.top_k,
            s.knowledge.match_floor) == (500, 4000, 8, 0.6)
    assert s.clarify.enabled is True
    assert (s.profiler.sample_rows, s.profiler.max_probes, s.profiler.time_budget_s, s.profiler.min_overlap,
            s.profiler.enum_max_distinct, s.profiler.max_cost_usd) == (1000, 200, 60, 0.9, 50, 0.50)
    assert s.review.max_age_days == 365


@pytest.mark.parametrize("update", [
    {"knowledge": {"item_max_chars": 0}},
    {"knowledge": {"match_floor": 1.5}},
    {"profiler": {"min_overlap": 2}},
    {"profiler": {"max_probes": 0}},
    {"review": {"max_age_days": 0}},
    {"clarify": {"enable": True}},
])
def test_new_settings_are_checked(update):
    with pytest.raises(ValidationError):
        Settings.model_validate(update)


def test_indexing_enrichment_agent_is_gone():
    assert "indexing_enrichment" not in get_args(AgentName)
    assert "profiler" in get_args(AgentName)
    from nl2sql.profiler.drafts import ENRICHMENT_LLM_NAME

    assert ENRICHMENT_LLM_NAME == "profiler"


def test_the_old_agent_name_is_an_error_that_names_the_new_one(tmp_path):
    """No alias (no active users): the 0.7 key fails, and the message says what it became."""
    with pytest.raises(ConfigError) as caught:
        load_config(_project(tmp_path, llm={"provider": "openai", "model": "gpt-4o",
                                            "agents": {"indexing_enrichment": {"model": "gpt-4o-mini"}}}))
    text = str(caught.value)
    assert "llm.agents.indexing_enrichment" in text and "renamed to 'profiler'" in text


# ---------------------------------------------------------------- the registry

def test_registry_lists_knowledge_sources():
    assert GROUPS["knowledge_source"] == "nl2sql.knowledge_sources"
    assert INTERFACE_VERSIONS["knowledge_source"] == 1
    registry = Registry.empty()
    source = types.SimpleNamespace(interface_version=1)
    registry.add_knowledge_source("wiki", source)
    registry.knowledge_source("catalog")(source)
    assert registry.names("knowledge_source") == ["catalog", "wiki"]
    assert registry.load("knowledge_source", "wiki") is source
    registry.add_knowledge_source("old", types.SimpleNamespace(interface_version=2))
    with pytest.raises(ValueError, match="knowledge source interface 2; this engine needs 1"):
        registry.load("knowledge_source", "old")


# ---------------------------------------------------------------- errors

ENDING = ["NEEDS_CLARIFICATION", "CLARIFICATION_INVALID", "CLARIFICATION_STALE", "COST_LIMIT_EXCEEDED"]


@pytest.mark.parametrize("code", ENDING)
def test_wave_b_codes_exist_and_are_never_retried(code):
    error = PipelineError(node="n", message="m", severity=ErrorSeverity.ERROR, error_code=ErrorCode(code))
    assert not error.is_retryable


def test_review_error_carries_its_code_and_fix():
    from nl2sql.review.models import ReviewError, ReviewErrorCode

    error = ReviewError(ReviewErrorCode.NOT_WRITABLE, "No yaml knowledge source.",
                        fix="Add a yaml knowledge source, or export and edit by hand.")
    assert error.code == ReviewErrorCode.NOT_WRITABLE and "export and edit" in str(error)
    assert {c.value for c in ReviewErrorCode} == {"NOT_AUTHORIZED", "NOT_WRITABLE", "FILE_CHANGED", "NOT_FOUND"}


# ---------------------------------------------------------------- events

NEW_EVENTS = {"knowledge.loaded": False, "knowledge.used": True, "clarification.requested": True,
              "cost.estimated": True, "profile.finished": False, "proposal.created": False,
              "proposal.decided": False}


def test_new_events_are_catalogued():
    from nl2sql.events.catalog import AUDIT_EVENTS, CATALOG, STREAM_EVENTS

    for name, run in NEW_EVENTS.items():
        assert CATALOG[name].run is run, name
        assert name not in STREAM_EVENTS, f"{name} is an operator event (D17)"
    assert {"proposal.decided", "cost.estimated"} <= AUDIT_EVENTS
    cost = CATALOG["cost.estimated"]
    assert cost.audited({"decision": "refuse"}) and cost.audited({"decision": "confirm"})
    assert not cost.audited({"decision": "allow"})
    CATALOG["cost.estimated"].model(datasource_id="d", sub_query_id="s", estimated_rows=10, cost=None, limit=5,
                                    decision="confirm")
    CATALOG["proposal.decided"].model(id="i", kind="join", datasource_id="d", decision="accepted", by="ada",
                                      wrote="glossary.yaml")
    with pytest.raises(ValidationError):
        CATALOG["knowledge.used"].model(sub_query_id="s", datasource_id="d", rows=[[1]])


# ---------------------------------------------------------------- results and state

def test_query_result_has_clarification_and_explanation():
    from nl2sql.api.query_api import SubQueryResult
    from nl2sql.clarify.models import ClarificationChoice, ClarificationOption, ClarificationRequest
    from nl2sql.explain.models import ExplainedStep, Explanation

    result = QueryResult()
    assert result.clarification is None and result.explanation is None
    assert SubQueryResult().knowledge_used is None and SubQueryResult().estimate is None

    step = ExplainedStep(sub_query_id="sq1", datasource_id="chinook", source="generated", text="Counts invoices.")
    # Every plan_source a sub-query can have is a source an explanation names ("llm" is "generated").
    for source in ("cache", "supplied", "approved", "chosen", "verified"):
        ExplainedStep(datasource_id="chinook", source=source, text="t")
    option = ClarificationOption(id="a", label="Revenue", plan={"tables": []}, explanation=step)
    request = ClarificationRequest.build(question="sales last year", asker_user_id="ada", asker_tenant_id=None,
                                         phrase="sales", datasource_id="chinook", schema_version="v1",
                                         knowledge_digest="k1", sub_query={"id": "sq1"},
                                         options=[option, option.model_copy(update={"id": "b"})])
    assert len(request.id) == 32 and request.id == request.expected_id()
    assert request.model_copy(update={"phrase": "revenue"}).expected_id() != request.id
    choice = ClarificationChoice(request=request, option_id="b")

    result = QueryResult(status="needs_clarification", clarification=request,
                         explanation=Explanation(steps=[step]))
    again = QueryResult.model_validate_json(result.model_dump_json())
    assert again.clarification == request and again.explanation.steps[0].text == "Counts invoices."
    assert ClarificationChoice.model_validate_json(choice.model_dump_json()) == choice


def test_planner_answer_and_the_planner_response_carry_alternatives():
    from nl2sql.pipeline.nodes.ast_planner.schemas import (ASTPlannerResponse, PlanAlternative, PlannerAnswer,
                                                           PlanModel)

    plan = PlanModel.model_validate({"tables": [{"name": "Invoice", "alias": "i"}]})
    answer = PlannerAnswer(plan=plan)
    assert answer.alternatives == [] and answer.ambiguous_phrase is None
    with pytest.raises(ValidationError):
        PlannerAnswer(plan=plan, alternatives=[PlanAlternative(label="x", plan=plan)] * 3)
    with pytest.raises(ValidationError):
        PlanAlternative(label="x" * 61, plan=plan)
    response = ASTPlannerResponse(plan=plan, plan_source="chosen", ambiguous_phrase="sales",
                                  alternatives=[PlanAlternative(label="Invoiced total", plan=plan)])
    assert response.alternatives[0].label == "Invoiced total" and response.plan_source == "chosen"


def test_the_state_carries_the_knowledge_a_step_used():
    from nl2sql.knowledge.models import KnowledgeUsed, RuleUse, TermMatch
    from nl2sql.pipeline.state import SubgraphExecutionState
    from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput

    used = KnowledgeUsed(datasource_id="chinook", digest="k1",
                         terms=[TermMatch(term="revenue", definition="d", columns=["InvoiceLine.UnitPrice"],
                                          source="yaml", score=1.0)],
                         rules=[RuleUse(id="no_test_customers", text="t", source="yaml")])
    assert SubgraphExecutionState(trace_id="t").knowledge_used is None
    assert SubgraphExecutionState(trace_id="t", knowledge_used=used).knowledge_used == used
    output = SubgraphOutput(subgraph_id="s", knowledge_used=used, estimate=Estimate(rows=5, source="x"))
    assert output.alternatives == [] and output.ambiguous_phrase is None and output.estimate.rows == 5


def test_the_result_reads_the_knowledge_used_and_estimate_from_the_output():
    from nl2sql.api.query_api import result_from_state
    from nl2sql.knowledge.models import KnowledgeUsed
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
    from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput

    used = KnowledgeUsed(datasource_id="chinook", digest="k1")
    sub_query = SubQuery(id="sq1", datasource_id="chinook", intent="count invoices")
    output = SubgraphOutput(subgraph_id="s", sub_query=sub_query, knowledge_used=used,
                            estimate=Estimate(rows=5, source="x"))
    result = result_from_state({"subgraph_outputs": {"s": output}})
    assert result.sub_queries[0].knowledge_used == used and result.sub_queries[0].estimate.rows == 5


def test_profile_report_and_accept_report_shapes():
    from nl2sql.profiler.models import ProfileReport
    from nl2sql.review.models import AcceptReport

    report = ProfileReport(datasource_id="chinook", schema_version="v1", seconds=1.5)
    assert (report.proposals, report.probes, report.stopped_by, report.cost) == ({}, 0, "done", None)
    with pytest.raises(ValidationError):
        ProfileReport(datasource_id="c", schema_version="v", seconds=1, stopped_by="tired")
    accepted = AcceptReport(accepted=[_proposal()], files_written=["glossary.yaml"])
    assert accepted.warnings == [] and accepted.refused == {}


# ---------------------------------------------------------------- the facade and the CLI

def test_the_cli_has_the_knowledge_review_and_profile_groups():
    from nl2sql.cli.main import app

    commands = typer.main.get_command(app).commands
    assert {"knowledge", "review", "profile"} <= set(commands)


def test_the_new_names_are_exported():
    import nl2sql

    for name in ("KnowledgeSourceConfig", "CostRule", "Proposal", "AcceptReport", "ReviewError", "ProfileReport",
                 "Explanation", "ExplainedStep", "TermUse", "ClarificationRequest", "ClarificationOption",
                 "ClarificationChoice", "KnowledgeSourceInfo", "DatasourceKnowledgeView"):
        assert getattr(nl2sql, name) is not None, name
    assert nl2sql.Proposal is Proposal
