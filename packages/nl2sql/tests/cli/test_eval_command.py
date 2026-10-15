"""``nl2sql eval``: run (with the gate and its exit codes), regenerate, seed and publish.

The engine is a stand-in whose ``evals`` is the real ``EvalsAPI`` with
``run`` and ``regenerate`` replaced, so the gate and the baseline loading
are the real ones and nothing runs a question.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql.api.evals_api import EvalsAPI
from nl2sql.cli.main import app
from nl2sql.config.models import EvalsConfig
from nl2sql.evaluation import report as report_module
from nl2sql.evaluation.regenerate import RegenerateReport
from nl2sql.evaluation.report import EvalReport
from nl2sql.evaluation.run import node_agents
from nl2sql.evaluation.seed import SeedReport
from nl2sql.llm.models import LLMFileConfig

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
DIGEST = "a" * 64
CHINOOK = {"datasource_id": "chinook", "engine": "sqlite", "schema_fingerprint": "f" * 16,
           "tables": 11, "columns": 64}


def _case(qid, rows=True, cost=0.01):
    scores = {"result_match": {"passed": rows, "value": None, "detail": {}},
              "outcome": {"passed": True, "value": None, "detail": {}}}
    status, reason = report_module.case_status(scores)
    return {"id": qid, "role": "admin", "pass": 1, "expected": "allowed", "status": status, "reason": reason,
            "lenient_status": status, "scores": scores, "tags": [], "difficulty": "easy", "cost": cost,
            "latency_s": 1.0, "sql": "SELECT 1", "rows_digest": "d", "refused_unanswerable": False, "retries": 0,
            "error_codes": [], "tokens_by_node": {}, "timings": {}, "source": "generated", "generated": None}


def _report(failing=0, *, mode="full", name="default", stopped=None, digest=DIGEST, n=10):
    cases = [_case(f"q{i}", rows=i >= failing) for i in range(n)]
    board = report_module.score_config(cases, passes=1)
    board.update(planned_cases=n, completed_cases=n, models={"ast_planner": "openai:gpt-5.4"})
    return EvalReport(mode=mode, suites=[{"name": "chinook", "path": "evals/chinook.yaml", "digest": digest}],
                      configs={name: board}, database=CHINOOK, stopped=stopped, max_cost_usd=5.0, spent=0.1)


class _Evals(EvalsAPI):
    """The real facade, with ``run`` and ``regenerate`` answering from what the test gives."""

    def __init__(self, folder, result, regenerated=None):
        super().__init__(SimpleNamespace(config=SimpleNamespace(folder=folder, evals=EvalsConfig())))
        self.result, self.regenerated, self.calls = result, regenerated, []
        self.seeded = None

    def run(self, suites=None, **kwargs):
        self.calls.append({"suites": suites, **kwargs})
        for record in self.result.configs[next(iter(self.result.configs))]["results"]:
            if kwargs.get("on_case"):
                kwargs["on_case"](next(iter(self.result.configs)), record, 0.01)
        return self.result

    def regenerate(self, path, *, allow_packaged=False):
        self.calls.append({"regenerate": path})
        return self.regenerated

    def seed(self, datasource_id, **kwargs):
        self.calls.append({"seed": datasource_id, **kwargs})
        if isinstance(self.seeded, Exception):
            raise self.seeded
        return self.seeded


class _Engine:
    def __init__(self, evals, folder):
        self.evals = evals
        self.config = SimpleNamespace(folder=folder, policies=SimpleNamespace(default_role=None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Point ``nl2sql eval`` at a stand-in engine; set ``engine.evals.result`` to what ``run`` returns."""
    monkeypatch.delenv("CI", raising=False)
    evals = _Evals(tmp_path, _report())
    stub = _Engine(evals, tmp_path)
    monkeypatch.setattr("nl2sql.cli.commands.eval.build_engine", lambda ctx, **kw: stub)
    return stub


def _out(result):
    return ANSI.sub("", result.output)


def _baseline(tmp_path, report):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(report.model_dump(mode="json")), encoding="utf-8")
    return path


def test_eval_run_exits_1_on_a_regression(engine, tmp_path):
    base = _baseline(tmp_path, _report())
    engine.evals.result = _report(failing=4)

    result = runner.invoke(app, ["eval", "run", "--baseline", str(base)])

    assert result.exit_code == 1, _out(result)
    assert "Regression" in _out(result) and "flipped from pass to fail" in _out(result)

    engine.evals.result = _report(failing=1)
    assert runner.invoke(app, ["eval", "run", "--baseline", str(base)]).exit_code == 0


def test_eval_run_exits_2_when_the_baseline_is_another_suite(engine, tmp_path):
    base = _baseline(tmp_path, _report(digest="b" * 64))
    result = runner.invoke(app, ["eval", "run", "--baseline", str(base)])
    assert result.exit_code == 2, _out(result)
    assert "Not compared" in _out(result) and "suite" in _out(result)


def test_eval_run_exits_3_at_the_cost_cap(engine, tmp_path):
    engine.evals.result = _report(stopped="max_cost")
    result = runner.invoke(app, ["eval", "run", "--max-cost", "1"])
    assert result.exit_code == 3, _out(result)
    assert "Stopped early" in _out(result)
    assert engine.evals.calls[0]["max_cost_usd"] == 1.0


def test_eval_run_writes_the_report_with_when_and_which_code(engine, tmp_path):
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["eval", "run", "--mode", "plans", "--cases", "join", "--cases", "chinook_001",
                                 "--suite", "evals/chinook.yaml", "--passes", "2", "--out", str(out)])
    assert result.exit_code == 0, _out(result)
    call = engine.evals.calls[0]
    assert call["mode"] == "plans" and call["cases"] == ["join", "chinook_001"] and call["passes"] == 2
    assert [s.as_posix() for s in call["suites"]] == ["evals/chinook.yaml"]
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["mode"] == "full" and written["configs"]["default"]["accuracy"]["execution"] == 1.0
    assert set(written["run"]) == {"recorded_at", "engine_version", "git_commit", "git_dirty"}
    assert "execution accuracy 100.0%" in _out(result)


def test_eval_run_writes_its_report_next_to_nl2sql_yaml_by_default(engine, tmp_path):
    engine.evals.result = _report(mode="retrieval", name="retrieval")
    assert runner.invoke(app, ["eval", "run", "--mode", "retrieval"]).exit_code == 0
    assert json.loads((tmp_path / "eval_retrieval.json").read_text(encoding="utf-8"))["mode"] == "retrieval"


def test_eval_run_llm_takes_a_preset_or_a_file(engine, tmp_path):
    own = tmp_path / "mine.yaml"
    own.write_text(yaml.safe_dump({"provider": "openai", "model": "gpt-5.4-mini"}), encoding="utf-8")

    result = runner.invoke(app, ["eval", "run", "--max-cost", "5", "--llm", "gpt-5.4", "--llm", f"own={own}",
                                 "--llm", "fast=mini-helpers", "--llm", str(own)])

    assert result.exit_code == 0, _out(result)
    llm = engine.evals.calls[0]["llm"]
    assert list(llm) == ["gpt-5.4", "own", "fast", "mine"]
    assert all(isinstance(cfg, LLMFileConfig) for cfg in llm.values())
    assert llm["own"].default.model == "gpt-5.4-mini" and llm["mine"].default.model == "gpt-5.4-mini"
    assert llm["gpt-5.4"].default.model == "gpt-5.4"
    assert node_agents(llm["fast"])["decomposer"].model == "gpt-5.4-mini"


def test_eval_run_refuses_an_unknown_preset_or_a_repeated_name(engine):
    assert runner.invoke(app, ["eval", "run", "--llm", "no-such-preset"]).exit_code == 2
    assert runner.invoke(app, ["eval", "run", "--llm", "gpt-5.4", "--llm", "gpt-5.4"]).exit_code == 2
    assert engine.evals.calls == []


def test_mode_full_never_runs_in_ci(engine, monkeypatch):
    monkeypatch.setenv("CI", "true")
    result = runner.invoke(app, ["eval", "run", "--mode", "full", "--max-cost", "5"])
    assert result.exit_code == 2 and "CI" in _out(result)
    assert engine.evals.calls == []
    assert runner.invoke(app, ["eval", "run", "--mode", "plans"]).exit_code == 0


def test_eval_run_reports_a_refused_run_as_a_usage_error(engine):
    def refuse(suites=None, **kwargs):
        raise ValueError("Mode full spends money: it needs a positive max_cost_usd")

    engine.evals.run = refuse
    result = runner.invoke(app, ["eval", "run"])
    assert result.exit_code == 2 and "max_cost_usd" in _out(result)


def test_eval_regenerate_calls_the_engine(engine, tmp_path):
    engine.evals.regenerated = RegenerateReport(path=tmp_path / "evals" / "mine.yaml", written=3)
    result = runner.invoke(app, ["eval", "regenerate", "evals/mine.yaml"])
    assert result.exit_code == 0, _out(result)
    assert engine.evals.calls == [{"regenerate": "evals/mine.yaml"}] and "3" in _out(result)

    engine.evals.regenerated = RegenerateReport(path=tmp_path / "x.yaml", written=1,
                                                failed=[{"case": "c1", "error": "no such table"}])
    failed = runner.invoke(app, ["eval", "regenerate", "x.yaml"])
    assert failed.exit_code == 1 and "c1" in _out(failed) and "no such table" in _out(failed)


def test_eval_seed_asks_as_the_role_and_prints_the_report(engine, tmp_path):
    engine.evals.seeded = SeedReport(path=tmp_path / "evals" / "sales.yaml", written=4, dropped_invalid=2,
                                     dropped_empty=1)
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--count", "8", "--role", "analyst",
                                 "--max-cost", "0.5", "--out", "evals/sales.yaml"])
    assert result.exit_code == 0, _out(result)
    [call] = engine.evals.calls
    assert call["seed"] == "sales" and call["count"] == 8 and call["max_cost_usd"] == 0.5
    assert call["out"] == "evals/sales.yaml" and call["user_context"].roles == ["analyst"]
    out = _out(result)
    assert "Wrote 4 draft" in out and "2 invalid" in out and "1 empty" in out


def test_eval_seed_takes_the_default_role_and_refuses_without_one(engine):
    engine.evals.seeded = SeedReport(path="s.yaml", written=1)
    refused = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--out", "s.yaml"])
    assert refused.exit_code == 2 and "--role" in _out(refused)
    assert engine.evals.calls == []

    engine.config.policies.default_role = "viewer"
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--out", "s.yaml"])
    assert result.exit_code == 0, _out(result)
    assert engine.evals.calls[0]["user_context"].roles == ["viewer"] and engine.evals.calls[0]["count"] == 20


def test_eval_seed_exits_3_at_the_call_limit(engine):
    engine.evals.seeded = SeedReport(path="s.yaml", written=0, stopped_at_call_limit=True)
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--role", "r", "--out", "s.yaml"])
    assert result.exit_code == 3 and "call limit" in _out(result)


def test_eval_seed_reports_hooked_drops(engine):
    engine.evals.seeded = SeedReport(path="s.yaml", written=1, dropped_hooked=2)
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--role", "r", "--out", "s.yaml"])
    assert result.exit_code == 0 and "2 stopped by a hook" in _out(result)


def test_eval_seed_exits_3_at_the_cost_cap(engine):
    engine.evals.seeded = SeedReport(path="s.yaml", written=5, stopped_at_cost_cap=True)
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--role", "r", "--out", "s.yaml"])
    assert result.exit_code == 3 and "cost cap" in _out(result)


@pytest.mark.parametrize("error", [PermissionError("The role(s) r may not read the datasource 'sales'."),
                                   ValueError("s.yaml holds approved cases; drafts never go into it.")])
def test_eval_seed_reports_a_refusal_as_a_usage_error(engine, error):
    engine.evals.seeded = error
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--role", "r", "--out", "s.yaml"])
    assert result.exit_code == 2 and str(error).split(" ")[1] in _out(result)


def test_seeding_never_runs_in_ci(engine, monkeypatch):
    monkeypatch.setenv("CI", "true")
    result = runner.invoke(app, ["eval", "seed", "--datasource", "sales", "--role", "r", "--out", "s.yaml"])
    assert result.exit_code == 2 and "CI" in _out(result)
    assert engine.evals.calls == []


def test_publish_writes_a_record(tmp_path):
    report_path = tmp_path / "eval_full.json"
    stamp = {"recorded_at": "2026-10-15T09:00:00Z", "engine_version": "0.7.0",
             "git_commit": "1234567890abcdef1234567890abcdef12345678", "git_dirty": False}
    report_path.write_text(json.dumps({**_report(name="gpt-5.4").model_dump(mode="json"), "run": stamp}),
                           encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("# x\n<!-- BENCHMARKS:START -->\n<!-- BENCHMARKS:END -->\n", encoding="utf-8")
    history = tmp_path / "docs" / "benchmarks.md"

    result = runner.invoke(app, ["eval", "publish", str(report_path), "--note", "first 0.7 run",
                                 "--results-dir", str(tmp_path / "benchmarks"), "--history-path", str(history),
                                 "--readme-path", str(readme)])

    assert result.exit_code == 0, _out(result)
    [path] = list((tmp_path / "benchmarks").glob("*/*/*.json"))
    assert path.relative_to(tmp_path / "benchmarks").as_posix() == "full/chinook/2026-10-15_1234567_gpt-5.4.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["note"] == "first 0.7 run" and record["recorded_at"] == stamp["recorded_at"]
    assert record["git_commit"] == stamp["git_commit"] and record["engine_version"] == "0.7.0"
    assert "gpt-5.4" in history.read_text(encoding="utf-8") and "gpt-5.4" in readme.read_text(encoding="utf-8")

    # Without a report it only rebuilds the pages from the records already there.
    again = runner.invoke(app, ["eval", "publish", "--results-dir", str(tmp_path / "benchmarks"),
                                "--history-path", str(history), "--readme-path", str(readme)])
    assert again.exit_code == 0 and len(list((tmp_path / "benchmarks").glob("*/*/*.json"))) == 1


def test_a_missing_or_unreadable_baseline_is_a_usage_error_not_a_traceback(engine, tmp_path):
    missing = runner.invoke(app, ["eval", "run", "--baseline", str(tmp_path / "nope.json")])
    assert missing.exit_code == 2, _out(missing)
    assert "Traceback" not in _out(missing) and "baseline" in _out(missing).lower()

    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert runner.invoke(app, ["eval", "run", "--baseline", str(garbage)]).exit_code == 2

    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"kind": "full"}), encoding="utf-8")
    assert runner.invoke(app, ["eval", "run", "--baseline", str(malformed)]).exit_code == 2


def test_a_series_break_still_prints_the_regressions_of_the_configs_it_compared(engine, tmp_path):
    good, bad = _report(), _report(failing=5)
    engine.evals.result = bad.model_copy(update={"configs": {"default": bad.configs["default"],
                                                             "mini": bad.configs["default"]}})
    def record(name, digest):
        return {"kind": "full", "dataset": {"sha256": digest}, "database": CHINOOK, "roles": None,
                "curated": False, "config": {"name": name}, "scoreboard": good.configs["default"]}
    path = tmp_path / "records.json"
    path.write_text(json.dumps([record("default", DIGEST), record("mini", "c" * 64)]), encoding="utf-8")

    result = runner.invoke(app, ["eval", "run", "--baseline", str(path)])

    out = _out(result)
    assert result.exit_code == 2, out
    assert out.index("Regression: default") < out.index("Not compared")
