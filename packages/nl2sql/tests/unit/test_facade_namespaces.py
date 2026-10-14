"""The facade's namespaces: one test per method in docs/architecture/configuration_v2.md, section 7.

Each builds a real engine on a ``make_config`` folder with a small SQLite
database and a fake embedder, so nothing is downloaded and no model is called.
The results namespace is covered in ``test_result_paging.py``, the curated one
in ``test_cli_curated_validate.py`` and ``test_cli_curated_test.py`` as well.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import (
    LLM,
    NL2SQL,
    ConfigError,
    Datasource,
    DatasourceInfo,
    DatasourceStatus,
    Embeddings,
    IndexProgress,
    LLMChange,
    Registry,
    UserContext,
)
from nl2sql.testing.config import make_config
from nl2sql.testing import OpenConnection
from nl2sql_sdk import ConnectionCheck

SHOP = {"id": "shop", "type": "sqlite", "description": "Orders and customers",
        "connection": {"database": "shop.db"}}
BROKEN = {"id": "broken", "type": "sqlite", "connection": {"database": "no/such/folder/x.db"}}


def _shop(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO customers VALUES (1, 'Ada')")
    return path


def _engine(tmp_path: Path, registry=None, **sections) -> NL2SQL:
    _shop(tmp_path / "shop.db")
    sections.setdefault("datasources", [SHOP])
    cfg = make_config(tmp_path, **sections)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}),
                  registry=registry)


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        yield engine


class SecretAdapter:
    """An adapter that takes a password and never connects."""

    interface_version = 2
    title = "Stub"
    connection_model = OpenConnection
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id=None, connection=None, limits=None, *, folder=None):
        self.datasource_id = datasource_id
        self.connection_args = connection.model_dump() if connection is not None else {}

    def capabilities(self):
        return set()

    def test_connection(self):
        return ConnectionCheck(ok=True)

    def get_dialect(self):
        return "postgres"


# ---------------------------------------------------------------- datasources

def test_datasources_list_reports_every_configured_datasource_and_its_status(tmp_path):
    with _engine(tmp_path, datasources=[SHOP, BROKEN]) as engine:
        before = engine.datasources.list()
        assert [(d.id, d.status) for d in before] == [("shop", "unchecked"), ("broken", "unchecked")]

        engine.check()
        after = {d.id: d for d in engine.datasources.list()}

    assert all(isinstance(d, DatasourceInfo) for d in before)
    assert after["shop"].status == "ready" and after["shop"].type == "sqlite"
    assert after["shop"].description == "Orders and customers" and after["shop"].capabilities
    assert after["broken"].status == "failed" and after["broken"].reason
    assert after["broken"].capabilities == []


def test_datasources_get_masks_the_connection(tmp_path):
    registry = Registry.default()
    registry.add_adapter("secretive", SecretAdapter)
    datasource = {"id": "dw", "type": "secretive", "connection": {"host": "db.local", "password": "hunter2"}}
    with _engine(tmp_path, registry=registry, datasources=[datasource]) as engine:
        info = engine.datasources.get("dw")
        with pytest.raises(ValueError, match="Unknown datasource ID: nope"):
            engine.datasources.get("nope")

    assert info.connection == {"host": "db.local", "password": "***"}
    assert "hunter2" not in repr(info)


def test_datasources_add_joins_the_config(engine, tmp_path):
    _shop(tmp_path / "other.db")
    engine.datasources.add(Datasource(id="other", type="sqlite",
                                      connection={"database": (tmp_path / "other.db").as_posix()}))

    assert [d.id for d in engine.config.datasources] == ["shop", "other"]
    assert engine.datasources.get("other").status == "unchecked"


def test_datasources_remove_leaves_the_config_and_routing(engine):
    engine.datasources.remove("shop")

    assert engine.config.datasources == []
    assert engine.datasources.list() == []
    with pytest.raises(ValueError, match="Unknown datasource ID: shop"):
        engine.datasources.remove("shop")


def test_datasources_schema_is_empty_before_the_first_index(engine):
    schema = engine.datasources.schema("shop")

    assert schema["tables"] == []


def test_datasources_test_connects_one_datasource(tmp_path):
    with _engine(tmp_path, datasources=[SHOP, BROKEN]) as engine:
        ready = engine.datasources.test("shop")
        failed = engine.datasources.test("broken")

    assert isinstance(ready, DatasourceStatus) and ready.status == "ready"
    assert failed.status == "failed" and failed.stage == "database"


# ---------------------------------------------------------------- index

def test_index_build_reports_each_datasource_as_it_goes(engine):
    progress = []

    result = engine.index.build(on_datasource=progress.append)

    assert result.ok
    assert [(p.datasource_id, p.state) for p in progress] == [("shop", "started"), ("shop", "done")]
    assert all(isinstance(p, IndexProgress) for p in progress)
    assert progress[-1].counts and all(isinstance(v, int) for v in progress[-1].counts.values())


def test_index_build_reports_a_failed_datasource_with_its_reason(engine):
    progress = []

    result = engine.index.build(["nope"], on_datasource=progress.append)

    assert not result.ok
    assert progress == [IndexProgress("nope", "failed", reason="No such datasource is configured.")]


def test_index_health_reads_the_live_index(engine):
    assert engine.index.health()["status"] == "missing"
    engine.index.build()

    assert engine.index.health()["status"] == "ok"


def test_index_clear_empties_the_index(engine):
    engine.index.build()
    engine.index.clear()

    assert engine.index.health()["status"] == "missing"


def test_index_inspect_runs_the_mmr_search(engine):
    engine.index.build()

    report = engine.index.inspect("customers", k=2)

    assert {"picks", "dropped"} <= set(report)


# ---------------------------------------------------------------- llm

LLM_WITH_PLANNER = {"provider": "openai", "model": "gpt-test", "api_key": "sk-test",
                    "agents": {"ast_planner": {"model": "gpt-planner"}}}


def test_llm_list_names_each_agent_and_whether_it_inherits(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        listed = engine.llm.list()

    assert listed["default"] == {"provider": "openai", "model": "gpt-test", "temperature": 0.0, "inherited": False}
    assert listed["ast_planner"]["model"] == "gpt-planner" and listed["ast_planner"]["inherited"] is False
    assert listed["decomposer"] == {**listed["default"], "inherited": True}
    assert "api_key" not in str(listed)


def test_llm_get_returns_one_agent(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        assert engine.llm.get("ast_planner")["model"] == "gpt-planner"
        with pytest.raises(ValueError, match="Unknown agent 'planner'"):
            engine.llm.get("planner")


def test_llm_replace_swaps_the_whole_section(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        change = engine.llm.replace(LLM(provider="openai", model="gpt-other", api_key="sk-test"))
        assert isinstance(change, LLMChange)
        assert engine.llm.get("ast_planner") == {**engine.llm.get("default"), "inherited": True}
        assert engine.config.llm.model == "gpt-other"
        with pytest.raises(ConfigError):
            engine.llm.replace(LLM(provider="nope", model="x"))


def test_llm_replace_takes_the_section_as_written(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        engine.llm.replace({"provider": "openai", "model": "gpt-other", "api_key": "sk-test",
                            "agents": {"decomposer": {"model": "gpt-split"}}})

        assert engine.llm.get("decomposer")["model"] == "gpt-split"
        assert engine.config.llm.agents["decomposer"].model == "gpt-split"


def test_llm_replace_checks_the_section_as_the_file_is(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        with pytest.raises(ConfigError) as error:
            engine.llm.replace({"provider": "openai", "model": "gpt-other", "agents": {"ast_planer": {}}})
        assert "llm.agents.ast_planer" in str(error.value) and "Did you mean 'ast_planner'?" in str(error.value)
        assert engine.llm.get("ast_planner")["model"] == "gpt-planner"


def test_llm_replace_never_reads_the_environment_outside_a_secret_field(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "s3cr3t")
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        with pytest.raises(ConfigError, match="allowed only in a secret field") as error:
            engine.llm.replace({"provider": "openai", "model": "${env:SOME_SECRET}", "api_key": "sk-test"})
        assert "s3cr3t" not in str(error.value)


# ---------------------------------------------------------------- policies

POLICIES = {"roles": {
    "analyst": {"description": "Reads customers", "allow": ["shop.main.customers"]},
    "admin": {"description": "Everything", "allow": ["*"]},
}}


def test_policies_roles_lists_the_configured_roles(tmp_path):
    with _engine(tmp_path, policies=POLICIES) as engine:
        roles = engine.policies.roles()

    assert set(roles) == {"analyst", "admin"}
    assert roles["analyst"].description == "Reads customers"


def test_policies_allows_checks_one_table(tmp_path):
    with _engine(tmp_path, policies=POLICIES) as engine:
        analyst = UserContext(roles=["analyst"])
        assert engine.policies.allows(analyst, "shop", "customers")
        assert not engine.policies.allows(analyst, "shop", "orders")


def test_policies_scope_is_what_a_caller_may_read_in_one_datasource(tmp_path):
    with _engine(tmp_path, policies=POLICIES) as engine:
        scope = engine.policies.scope(UserContext(roles=["analyst"]), "shop")
        other = engine.policies.scope(UserContext(roles=["analyst"]), "elsewhere")

    assert scope.reaches_datasource() and scope.allows("main", "customers")
    assert not scope.allows("main", "orders")
    assert "no allow pattern" in scope.rule_for("main", "orders")
    assert not other.reaches_datasource()


def test_policies_validate_flags_a_pattern_for_an_unknown_datasource(tmp_path):
    policies = {"roles": {"analyst": {"description": "x", "allow": ["ghost", "shop.main.*"], "deny": ["*.audit.*"]}}}
    with _engine(tmp_path, policies=policies) as engine:
        report = engine.policies.validate()

    assert not report.ok
    assert [(e.target, e.status) for e in report.entries] == [
        ("Allow: ghost", "MISSING"), ("Allow: shop.main.*", "OK"), ("Deny: *.audit.*", "OK")]


# ---------------------------------------------------------------- curated

def test_curated_validate_without_a_curated_section_says_so(engine):
    report = engine.curated.validate()

    assert not report.ok and "no curated section" in report.errors[0]


def test_curated_test_without_a_curated_section_says_so(engine):
    report = engine.curated.test()

    assert not report.ok and "no curated section" in report.errors[0]


# ---------------------------------------------------------------- traces

def test_traces_replay_names_a_datasource_that_is_not_configured(engine):
    trace = {"trace_id": "t1", "request": {"question": "q", "datasource_id": "ghost"}, "result": {}}

    with pytest.raises(LookupError, match="ghost: not configured in this environment"):
        engine.traces.replay(trace)


TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


def _write_trace(engine, trace_id=TRACE_ID, **fields):
    """A trace file in the engine's traces folder, named as the trace writer names it."""
    import json

    folder = Path(engine.config.storage.path) / "traces"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"trace_format_version": 1, "trace_id": trace_id, "nodes": [], **fields}
    (folder / f"20260921T101112000000Z_{trace_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_traces_get_reads_one_trace_from_the_engines_folder(engine):
    _write_trace(engine)

    assert engine.traces.get(TRACE_ID)["trace_id"] == TRACE_ID
    assert engine.traces.get("not-recorded") is None
    with pytest.raises(ValueError):
        engine.traces.get("../outside")


# ---------------------------------------------------------------- feedback

def _answered(trace_id=TRACE_ID, sql="SELECT COUNT(*) FROM customers", codes=()):
    """A run's ``QueryResult`` as JSON, as a caller holds it after the question."""
    return {"trace_id": trace_id, "status": "success" if not codes else "error",
            "errors": [{"error_code": c} for c in codes],
            "sub_queries": [{"id": "sq1", "sql": sql, "datasource_id": "shop", "status": "success"}],
            "source": {"type": "generated"},
            "usage": {"calls": [{"node": "ast_planner", "model": "gpt-planner"}]}}


def test_feedback_record_stores_what_the_run_returned(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        saved = engine.feedback.record(_answered(), question="How many customers?", role="viewer",
                                       rating="down", note="wrong number")
        [row] = engine.feedback.list()

    assert saved["rating"] == "down" and row["trace_id"] == TRACE_ID
    assert row["question"] == "How many customers?" and row["role"] == "viewer" and row["note"] == "wrong number"
    assert row["sql"] == ["SELECT COUNT(*) FROM customers"] and row["datasource_ids"] == ["shop"]
    assert row["models"] == {"ast_planner": {"provider": "openai", "model": "gpt-planner"}}
    assert row["engine_version"]


def test_feedback_record_reads_the_runs_trace_and_keeps_no_sql_for_a_refused_run(engine):
    _write_trace(engine, nodes=[{"node": "logical_validator", "status": "error"}],
                 result={"errors": [{"error_code": "SECURITY_VIOLATION"}], "sub_queries": []})

    engine.feedback.record(_answered(), question="q", role="viewer", rating="down")
    [row] = engine.feedback.list()

    assert row["validator_failures"] == 1
    assert row["error_codes"] == ["SECURITY_VIOLATION"] and row["sql"] == []


def test_feedback_list_is_newest_first_and_takes_a_limit(engine):
    for trace_id in ("first", "second"):
        engine.feedback.record(_answered(trace_id), question=trace_id, role="admin", rating="up")

    assert [r["trace_id"] for r in engine.feedback.list()] == ["second", "first"]
    assert len(engine.feedback.list(limit=1)) == 1


def test_feedback_stats_counts_the_ratings_and_the_traces(engine):
    engine.feedback.record(_answered("rated"), question="q", role="admin", rating="up")
    _write_trace(engine, "traced", result={"errors": [{"error_code": "QUESTION_NOT_ANSWERABLE"}]})

    stats = engine.feedback.stats()

    assert stats["runs"] == 2 and stats["feedback"]["up"] == 1
    assert stats["refusals"]["by_code"] == {"QUESTION_NOT_ANSWERABLE": 1}
    assert engine.feedback.stats(traces=Path(engine.config.storage.path) / "elsewhere")["runs"] == 1


def test_feedback_export_writes_the_thumbs_up_runs_as_draft_gold_entries(engine, tmp_path):
    import yaml

    engine.feedback.record(_answered("one"), question="How many customers?", role="admin", rating="up")
    engine.feedback.record(_answered("two"), question="Bad", role="admin", rating="down")

    report = engine.feedback.export(tmp_path / "drafts.yaml")

    drafts = yaml.safe_load((tmp_path / "drafts.yaml").read_text(encoding="utf-8"))
    assert report == {"written": 1, "skipped": 0}
    assert [d["question"] for d in drafts] == ["How many customers?"]


def test_feedback_export_never_writes_the_gold_set(engine):
    from nl2sql.evaluation.gold import GOLD_DATASET_PATH

    before = GOLD_DATASET_PATH.read_bytes()
    with pytest.raises(ValueError, match="gold set"):
        engine.feedback.export(GOLD_DATASET_PATH)
    assert GOLD_DATASET_PATH.read_bytes() == before


def test_feedback_clear_empties_the_table(engine):
    engine.feedback.record(_answered(), question="q", role="admin", rating="up")

    assert engine.feedback.clear() == 1
    assert engine.feedback.list() == []


# ---------------------------------------------------------------- about, providers, inspector defaults

def test_about_lists_each_step_with_its_agent_and_model_and_the_engine(tmp_path):
    with _engine(tmp_path, llm=LLM_WITH_PLANNER) as engine:
        about = engine.about()

    steps = {s["node"]: s for s in about["steps"]}
    assert about["version"] and "python" in about
    assert steps["ast_planner"]["agent"] == "astplanner" and steps["ast_planner"]["model"] == "gpt-planner"
    assert steps["decomposer"]["model"] == "gpt-test" and steps["decomposer"]["kind"] == "model"
    assert steps["generator"]["kind"] == "code" and steps["generator"]["model"] is None
    assert "sk-test" not in str(about)


def test_llm_providers_lists_the_installed_providers_and_whether_each_key_is_set(engine, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0000000000000000000000")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    providers = {p["name"]: p for p in engine.llm.providers()}

    assert providers["openai"] == {"name": "openai", "label": "OpenAI", "key_variable": "OPENAI_API_KEY",
                                   "key_present": True}
    assert providers["openrouter"]["key_present"] is False
    assert providers["ollama"]["key_variable"] is None
    assert "sk-test" not in str(providers)


def test_index_inspect_defaults_are_the_searchs_own(engine):
    defaults = engine.index.inspect_defaults()

    assert defaults["k"] == 8 and 0 < defaults["lambda_mult"] <= 1 and defaults["fetch_multiplier"] >= 1
    assert "schema.table" in defaults["types"]


# ---------------------------------------------------------------- questions

def test_run_query_takes_the_question_and_keyword_options(engine):
    import inspect

    for method in (engine.run_query, engine.stream_query):
        parameters = inspect.signature(method).parameters
        assert list(parameters)[0] == "question"
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for name, p in parameters.items() if name != "question")


# ---------------------------------------------------------------- the old shape is gone

REMOVED = ["context", "query", "datasource", "indexing", "auth", "settings", "policy",
           "get_result_manifest", "get_result_page", "export_result_csv", "add_datasource",
           "add_datasource_from_config", "list_datasources", "list_unavailable_datasources",
           "get_datasource_capabilities", "configure_llm", "configure_llm_from_config", "reload_llm_config",
           "list_llms", "get_llm", "index_datasource", "index_all_datasources", "clear_index", "rebuild_index",
           "index_health", "inspect_retrieval", "get_schema", "check_permissions", "get_allowed_resources",
           "get_current_settings", "get_setting", "validate_configuration"]


@pytest.mark.parametrize("name", REMOVED)
def test_the_04_methods_are_gone(engine, name):
    assert not hasattr(engine, name)


def test_datasources_has_no_get_adapter(engine):
    assert not hasattr(engine.datasources, "get_adapter")
