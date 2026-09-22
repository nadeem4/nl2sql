"""`nl2sql benchmark --tier 2`: the required cap, the config specs, the exit codes and the baseline.

The benchmark API is replaced, so no context is built and nothing is called;
the real run against a fake LLM is ``tests/e2e/test_benchmark_tier2_fake_llm.py``.
"""
import json

import pytest
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.configs.llm import AgentConfig, LLMFileConfig
from nl2sql.evaluation import tier2
from nl2sql.evaluation.gold import GOLD_DATASET_PATH

runner = CliRunner()


def _record(qid, status, cost=0.01):
    return {"id": qid, "question": "?", "role": "admin", "expected": "allowed", "status": status, "reason": "",
            "sql": "SELECT 1", "rows": 1, "gold_rows": 1, "pass": 1, "tags": ["join"], "difficulty": "easy",
            "cost": cost, "latency_s": 1.0, "rows_digest": "d", "error_codes": [], "refused_unanswerable": False,
            "retries": 0, "tokens_by_node": {"ast_planner": {"calls": 1, "input_tokens": 10, "cached_input_tokens": 4,
                                                             "cache_write_input_tokens": 0, "output_tokens": 2,
                                                             "reasoning_tokens": 1}},
            "timings": {"ast_planner": 0.4}}


def _board(stopped=None, statuses=("pass", "fail"), cost=0.01):
    configs = {}
    for name in ("a", "b"):
        board = tier2.score_config([_record(f"q{i}", s, cost) for i, s in enumerate(statuses)], passes=1)
        board.update(models={}, planned_cases=2, completed_cases=2)
        configs[name] = board
    return {"tier": 2, "dataset": str(GOLD_DATASET_PATH), "roles": ["admin"], "passes": 1, "questions": ["q0", "q1"],
            "max_cost": 5.0, "spent": 0.04, "stopped": stopped, "prices_checked_on": "2026-09-21",
            "configs": configs, "comparison": tier2.compare(configs)}


class _FakeAPI:
    calls = []
    board = None

    def tier2_configs(self, config, paths):
        _FakeAPI.calls.append(("configs", config, paths))
        return {name: LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4"))
                for name in (paths or {"default": None})}

    def run_tier2(self, config, configs, **kwargs):
        _FakeAPI.calls.append(("run", config, configs, kwargs))
        return _FakeAPI.board


@pytest.fixture(autouse=True)
def _api(monkeypatch):
    _FakeAPI.calls, _FakeAPI.board = [], _board()
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.benchmark.BenchmarkAPI", _FakeAPI)


def _invoke(tmp_path, *args):
    return runner.invoke(app, ["benchmark", "--tier", "2", "--export-path", str(tmp_path / "t2.json"),
                               "--results-dir", str(tmp_path / "results"), *args])


def test_tier2_refuses_to_start_without_max_cost(tmp_path):
    out = _invoke(tmp_path)
    assert out.exit_code == 2
    assert "--max-cost" in out.output
    assert _FakeAPI.calls == []


def test_tier2_refuses_to_run_in_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    out = _invoke(tmp_path, "--max-cost", "5")
    assert out.exit_code == 2 and _FakeAPI.calls == []


def test_a_config_spec_must_be_name_equals_path(tmp_path):
    out = _invoke(tmp_path, "--max-cost", "5", "--llm", "no-equals-sign")
    assert out.exit_code == 2 and _FakeAPI.calls == []


def test_tier2_runs_each_named_config_and_writes_the_scoreboard(tmp_path):
    out = _invoke(tmp_path, "--max-cost", "5", "--llm", "a=cfg/a.yaml", "--llm", "b=cfg/b.yaml",
                  "--passes", "2", "--questions", "chinook_001", "--questions", "join")
    assert out.exit_code == 0, out.output
    _, config, paths = _FakeAPI.calls[0]
    assert {k: str(v).replace("\\", "/") for k, v in paths.items()} == {"a": "cfg/a.yaml", "b": "cfg/b.yaml"}
    assert config.roles == ["admin"]  # the tier 2 default
    _, _, configs, kwargs = _FakeAPI.calls[1]
    assert set(configs) == {"a", "b"}
    assert kwargs["max_cost"] == 5.0 and kwargs["passes"] == 2
    assert kwargs["questions"] == ["chinook_001", "join"]
    body = json.loads((tmp_path / "t2.json").read_text(encoding="utf-8"))
    assert body["tier"] == 2 and set(body["configs"]) == {"a", "b"}
    assert "Tier 2 scoreboard" in out.output
    # One result record per config, named by date, engine version and config.
    written = sorted(p.name for p in (tmp_path / "results").glob("*.json"))
    assert len(written) == 2 and written[0].endswith("_a.json") and written[1].endswith("_b.json")


def test_publish_writes_the_history_page_and_the_readme_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("x\n<!-- BENCHMARKS:START -->\nold\n<!-- BENCHMARKS:END -->\n", encoding="utf-8")
    history = tmp_path / "benchmarks.md"
    args = ["benchmark", "publish", "--results-dir", str(tmp_path / "none"), "--history-path", str(history),
            "--readme-path", str(readme)]
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert "No benchmark runs recorded yet." in readme.read_text(encoding="utf-8")
    first = history.read_bytes()
    assert runner.invoke(app, args).exit_code == 0 and history.read_bytes() == first
    assert _FakeAPI.calls == []


def test_without_a_config_the_project_llm_config_runs_as_default(tmp_path):
    out = _invoke(tmp_path, "--max-cost", "5")
    assert out.exit_code == 0, out.output
    assert _FakeAPI.calls[0][2] is None


def test_a_run_stopped_by_the_cap_exits_three_with_the_partial_scoreboard(tmp_path):
    _FakeAPI.board = _board(stopped="max_cost")
    out = _invoke(tmp_path, "--max-cost", "5")
    assert out.exit_code == 3
    assert json.loads((tmp_path / "t2.json").read_text(encoding="utf-8"))["stopped"] == "max_cost"


def test_baseline_check_passes_and_fails(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(_board()), encoding="utf-8")
    assert _invoke(tmp_path, "--max-cost", "5", "--baseline", str(baseline)).exit_code == 0

    _FakeAPI.board = _board(statuses=("fail", "fail"))
    out = _invoke(tmp_path, "--max-cost", "5", "--baseline", str(baseline))
    assert out.exit_code == 1
    assert "accuracy fell" in out.output

    _FakeAPI.board = _board(cost=0.05)
    out = _invoke(tmp_path, "--max-cost", "5", "--baseline", str(baseline), "--max-cost-increase", "5")
    assert out.exit_code == 0, out.output
