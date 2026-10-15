"""The shapes Wave A's tracks share, added before any of them (docs/architecture/wave_a.md, D35).

The config sections and settings, the registry kinds, the error codes, the
result fields, the engine's bus and the stub ``engine.events`` and
``engine.evals`` namespaces.
"""
from __future__ import annotations

import sqlite3
import types
from pathlib import Path

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import ValidationError

from nl2sql import NL2SQL, Config, ConfigError, Embeddings, QueryResult, Registry, Settings, load_config
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.config import EvalsConfig, HookConfig, ObserverConfig
from nl2sql.config.models import AgentName
from nl2sql.registry import GROUPS, INTERFACE_VERSIONS
from nl2sql.testing.config import make_config

# ---------------------------------------------------------------- settings


def test_new_settings_have_the_designed_defaults():
    s = Settings()
    assert (s.pipeline.max_llm_calls, s.pipeline.max_cost_usd, s.pipeline.sanity_checks) == (60, None, True)
    assert (s.routing.candidates, s.routing.max_datasources, s.routing.max_sub_queries,
            s.routing.floor_ratio) == (3, 3, 6, 0.5)
    assert (s.retrieval.full_snapshot_max_tables, s.retrieval.table_k, s.retrieval.planning_k,
            s.retrieval.decomposer_tables) == (15, 8, 12, 30)


def test_caps_may_be_turned_off():
    s = Settings(pipeline={"max_llm_calls": None, "max_cost_usd": 0.5})
    assert (s.pipeline.max_llm_calls, s.pipeline.max_cost_usd) == (None, 0.5)


@pytest.mark.parametrize("update", [
    {"pipeline": {"max_llm_calls": 0}},
    {"pipeline": {"max_cost_usd": 0}},
    {"routing": {"floor_ratio": 1.5}},
    {"routing": {"max_datasources": 0}},
    {"retrieval": {"table_k": 0}},
    {"routing": {"max_datasource": 3}},
])
def test_new_settings_are_checked(update):
    with pytest.raises(ValidationError):
        Settings.model_validate(update)


def test_observability_setting_is_gone():
    assert "observability" not in Settings.model_fields
    with pytest.raises(ValidationError):
        Settings.model_validate({"observability": {"exporter": "otlp"}})


def test_eval_seeder_is_an_agent():
    from typing import get_args

    assert "eval_seeder" in get_args(AgentName)


# ---------------------------------------------------------------- the three sections

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


def test_no_observers_section_means_none_was_written(tmp_path):
    config = load_config(_project(tmp_path))
    assert config.observers is None, "absent means the defaults (log, audit), resolved when they are built"
    assert config.hooks == []
    assert config.evals == EvalsConfig()


def test_observers_and_hooks_are_lists_read_from_their_files(tmp_path):
    _write(tmp_path, "observers.yaml", [{"type": "log", "level": "DEBUG"},
                                        {"type": "corp_siem", "url": "${env:SIEM_URL}"}])
    _write(tmp_path, "hooks.yaml", [{"type": "corp_sql_review", "points": ["execute.before"],
                                     "on_error": "open", "approver_roles": ["dba"], "url": "https://x"}])
    _write(tmp_path, "evals.yaml", {"suites": ["evals/chinook.yaml"], "scorers": ["result_match"],
                                    "max_cost_usd": 5.0, "gate": {"max_regressions": 1}})

    config = load_config(_project(tmp_path, observers={"file": "observers.yaml"}, hooks={"file": "hooks.yaml"},
                                  evals={"file": "evals.yaml"}))

    log, siem = config.observers
    assert isinstance(log, ObserverConfig) and log.type == "log" and log.model_extra == {"level": "DEBUG"}
    assert siem.model_extra == {"url": "${env:SIEM_URL}"}, "a plugin's references are kept until it is built"
    hook = config.hooks[0]
    assert isinstance(hook, HookConfig)
    assert (hook.type, hook.points, hook.on_error, hook.approver_roles) == (
        "corp_sql_review", ["execute.before"], "open", ["dba"])
    assert hook.model_extra == {"url": "https://x"}
    assert config.evals.suites == [Path("evals/chinook.yaml")]
    assert (config.evals.max_cost_usd, config.evals.gate.max_regressions, config.evals.gate.alpha) == (5.0, 1, 0.05)
    assert config.locate("hooks[0].points") == ("hooks.yaml", "[0].points")


def test_an_empty_observers_list_means_none(tmp_path):
    assert load_config(_project(tmp_path, observers=[])).observers == []


def test_new_config_sections_are_strict(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_config(_project(tmp_path, evals={"suite": ["x.yaml"]}, hooks=[{"type": "h", "points": ["plan.before"]}],
                             observers=[{"level": "DEBUG"}]))
    text = str(caught.value)
    assert "Did you mean 'suites'" in text
    assert "hooks[0].points" in text
    assert "observers[0].type" in text


def test_hook_defaults():
    hook = HookConfig(type="question_rewriter")
    assert (hook.points, hook.on_error, hook.approver_roles) == (None, "closed", None)


def test_evals_defaults():
    evals = EvalsConfig()
    assert evals.suites == [] and evals.curated is True and evals.max_cost_usd is None
    assert evals.scorers == ["result_match", "outcome", "faithfulness", "retrieval_recall", "routing"]
    assert (evals.gate.max_regressions, evals.gate.alpha, evals.gate.max_cost_increase) == (2, 0.05, 0.20)


def test_objects_are_used_as_given_in_code(tmp_path):
    class Printer:
        def on_event(self, event):
            pass

    class Rewriter:
        points = frozenset({"run.before"})

        def __call__(self, ctx):
            return None

    printer, rewriter = Printer(), Rewriter()
    config = Config(**_sections(tmp_path), observers=[{"type": "log"}, printer], hooks=[rewriter])
    assert config.observers[1] is printer and config.hooks[0] is rewriter


@pytest.mark.parametrize("section, value", [("observers", ["log"]), ("hooks", [42])])
def test_a_block_or_an_object_is_required(tmp_path, section, value):
    with pytest.raises(ValidationError):
        Config(**_sections(tmp_path), **{section: value})


def _sections(tmp_path):
    base = make_config(tmp_path)
    return {name: getattr(base, name) for name in ("datasources", "llm", "embeddings", "policies", "storage")}


# ---------------------------------------------------------------- the registry

def test_registry_lists_observers_hooks_and_scorers():
    assert (GROUPS["observer"], GROUPS["hook"], GROUPS["scorer"]) == (
        "nl2sql.observers", "nl2sql.hooks", "nl2sql.scorers")
    assert INTERFACE_VERSIONS["observer"] == INTERFACE_VERSIONS["hook"] == INTERFACE_VERSIONS["scorer"] == 1
    registry = Registry.empty()
    thing = types.SimpleNamespace(interface_version=1)
    for kind in ("observer", "hook", "scorer"):
        getattr(registry, f"add_{kind}")("a", thing)
        getattr(registry, kind)("b")(thing)
        assert registry.names(kind) == ["a", "b"]
        assert registry.load(kind, "a") is thing


# ---------------------------------------------------------------- errors and the result

NOT_RETRYABLE = ["HOOK_DENIED", "HOOK_FAILED", "NEEDS_APPROVAL", "RUN_LIMIT_REACHED", "QUESTION_TOO_BROAD"]
RETRYABLE = ["JOIN_KEY_TYPE_MISMATCH", "JOIN_FAN_OUT"]
WARNINGS = ["JOIN_KEY_UNCONVERTED", "ALL_NULL_COLUMN"]


@pytest.mark.parametrize("code", NOT_RETRYABLE + RETRYABLE + WARNINGS)
def test_wave_a_error_codes_exist(code):
    assert ErrorCode(code).value == code


@pytest.mark.parametrize("code", NOT_RETRYABLE)
def test_ending_codes_are_never_retried(code):
    error = PipelineError(node="n", message="m", severity=ErrorSeverity.ERROR, error_code=ErrorCode(code))
    assert not error.is_retryable


@pytest.mark.parametrize("code", RETRYABLE)
def test_retry_codes_are_retried(code):
    error = PipelineError(node="n", message="m", severity=ErrorSeverity.ERROR, error_code=ErrorCode(code))
    assert error.is_retryable


def test_query_result_has_the_new_fields():
    from nl2sql.api.query_api import RunStatus, result_from_state
    from typing import get_args

    assert set(get_args(RunStatus)) == {"success", "plan_only", "error", "needs_approval", "stopped",
                                        "needs_clarification"}
    result = QueryResult()
    assert result.stop is None and result.approvals_needed == [] and result.status is None
    with pytest.raises(ValidationError):
        QueryResult(status="finished")
    assert result_from_state({}).status is None


def test_a_stop_in_the_state_ends_the_run_stopped():
    from nl2sql import RunStop
    from nl2sql.api.query_api import result_from_state

    result = result_from_state({"stop": {"limit": "max_llm_calls", "value": 60, "spent": 60}, "errors": [
        PipelineError(node="orchestrator", message="limit", severity=ErrorSeverity.ERROR,
                      error_code=ErrorCode.RUN_LIMIT_REACHED)]})

    assert result.status == "stopped"
    assert result.stop == RunStop(limit="max_llm_calls", value=60, spent=60)


def test_requests_in_the_state_end_the_run_needing_approval():
    from nl2sql import ApprovalRequest
    from nl2sql.api.query_api import result_from_state

    request = ApprovalRequest(id="abc", question="q", asker_user_id="ada", asker_tenant_id=None, hook="review",
                              point="execute.before", datasource_id="hr", sql="SELECT 1", sql_sha256="f00",
                              reason="Reads payroll.salaries", approver_roles=["dba"])
    result = result_from_state({"approvals_needed": [request.model_dump()], "errors": [
        PipelineError(node="executor", message="approval", severity=ErrorSeverity.ERROR,
                      error_code=ErrorCode.NEEDS_APPROVAL)]})

    assert result.status == "needs_approval"
    assert result.approvals_needed == [request]


# ---------------------------------------------------------------- the engine

def _engine(tmp_path: Path) -> NL2SQL:
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    cfg = make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite",
                                              "connection": {"database": "shop.db"}}])
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))


def test_two_engines_have_two_buses(tmp_path):
    with _engine(tmp_path / "a") as a, _engine(tmp_path / "b") as b:
        seen_a, seen_b = [], []
        a._runtime.bus.subscribe(seen_a.append)
        b._runtime.bus.subscribe(seen_b.append)
        a._runtime.bus.publish("index.failed", {"datasource_id": "shop", "reason": "x"})

        assert a._runtime.bus is not b._runtime.bus
        assert [e.name for e in seen_a] == ["index.failed"] and seen_b == []


def test_the_events_and_evals_namespaces_exist(tmp_path):
    """Stubs: E1 fills ``engine.events``, V1 ``engine.evals``. Neither loads nl2sql.evaluation."""
    from nl2sql.api.evals_api import EvalsAPI
    from nl2sql.api.events_api import EventsAPI

    with _engine(tmp_path) as engine:
        assert isinstance(engine.events, EventsAPI) and engine.events is engine.events
        assert isinstance(engine.evals, EvalsAPI) and engine.evals is engine.evals


def test_engine_close_closes_the_bus(tmp_path):
    engine = _engine(tmp_path)
    closed = []

    class Observer:
        def on_event(self, event):
            pass

        def close(self):
            closed.append(True)

    engine._runtime.bus.subscribe(Observer())
    engine.close()
    assert closed == [True]


def test_hook_point_is_importable_from_nl2sql_hooks():
    from nl2sql.config.models import HookPoint as defined
    from nl2sql.hooks import HookPoint

    assert HookPoint is defined


def test_verified_ref_is_exported_from_nl2sql():
    from nl2sql import VerifiedRef
    from nl2sql.hooks.approval import VerifiedRef as defined

    assert VerifiedRef is defined
