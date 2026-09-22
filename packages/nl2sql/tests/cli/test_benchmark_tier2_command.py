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


def test_an_llm_spec_that_is_no_preset_path_or_name_equals_path_is_a_usage_error(tmp_path):
    out = _invoke(tmp_path, "--max-cost", "5", "--llm", "no-such-preset")
    assert out.exit_code == 2 and _FakeAPI.calls == []
    assert "No preset named 'no-such-preset'" in out.output


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
    written = sorted(p.name for p in (tmp_path / "results" / "tier2" / "unknown").glob("*.json"))
    assert len(written) == 2 and written[0].endswith("_a.json") and written[1].endswith("_b.json")


def test_the_scoreboard_prints_faithfulness_and_each_unfaithful_answer(tmp_path):
    good, bad = _record("q0", "pass"), _record("q1", "pass")
    good["faithfulness"] = {"faithful": True, "unsupported_numbers": [], "unsupported_entities": [], "checked": 2}
    bad["faithfulness"] = {"faithful": False, "unsupported_numbers": ["1,300"], "unsupported_entities": ["Jazz"],
                           "checked": 3}
    board = tier2.score_config([good, bad], passes=1)
    board.update(models={}, planned_cases=2, completed_cases=2)
    _FakeAPI.board = {**_board(), "configs": {"a": board}, "comparison": tier2.compare({"a": board})}
    out = runner.invoke(app, ["benchmark", "--tier", "2", "--export-path", str(tmp_path / "t2.json"),
                              "--results-dir", str(tmp_path / "results"), "--max-cost", "5"],
                        env={"COLUMNS": "250"})
    assert out.exit_code == 0, out.output
    assert "Faithful" in out.output and "50.0%" in out.output
    assert "1,300, Jazz" in out.output


def test_publish_writes_the_history_page_and_the_readme_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("x\n<!-- BENCHMARKS:START -->\nold\n<!-- BENCHMARKS:END -->\n", encoding="utf-8")
    history = tmp_path / "benchmarks.md"
    args = ["benchmark", "publish", "--results-dir", str(tmp_path / "none"), "--history-path", str(history),
            "--readme-path", str(readme)]
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert "No tier 2 run recorded yet." in readme.read_text(encoding="utf-8")
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


CHINOOK = {"datasource_id": "chinook", "engine": "sqlite", "schema_fingerprint": "ceed80fefde051bd",
           "tables": 11, "columns": 64}


def test_models_and_presets_become_configs_in_one_comparison(tmp_path):
    from nl2sql.evaluation import presets

    out = _invoke(tmp_path, "--max-cost", "10", "--model", "gpt-5.4", "--model", "claude-opus-5",
                  "--llm", "mini-helpers", "--llm", str(tmp_path / "mine.yaml"))
    assert out.exit_code == 0, out.output
    _, _, choices = _FakeAPI.calls[0]
    assert list(choices) == ["gpt-5.4", "claude-opus-5", "gpt-5.4-mini-helpers", "mine"]
    assert isinstance(choices["gpt-5.4"], LLMFileConfig) and choices["gpt-5.4"].default.model == "gpt-5.4"
    assert choices["claude-opus-5"].agents["astplanner"].provider == "anthropic"
    assert choices["gpt-5.4-mini-helpers"] == presets.PRESETS_DIR / "gpt-5.4-mini-helpers.yaml"
    assert choices["mine"] == tmp_path / "mine.yaml"


def test_an_unknown_model_or_a_repeated_name_is_a_usage_error(tmp_path):
    out = _invoke(tmp_path, "--max-cost", "5", "--model", "gpt-9-turbo")
    assert out.exit_code == 2 and _FakeAPI.calls == []
    assert "provider/model" in out.output
    out = _invoke(tmp_path, "--max-cost", "5", "--model", "gpt-5.4", "--llm", "gpt-5.4")
    assert out.exit_code == 2 and "named 'gpt-5.4'" in out.output


def test_the_plan_is_printed_before_the_run(tmp_path, monkeypatch):
    printed_before_run = []
    run = _FakeAPI.run_tier2

    def spy(self, *args, **kwargs):
        printed_before_run.append(True)
        return run(self, *args, **kwargs)

    monkeypatch.setattr(_FakeAPI, "run_tier2", spy)
    out = _invoke(tmp_path, "--max-cost", "5", "--model", "gpt-5.4", "--questions", "chinook_001,chinook_002")
    assert out.exit_code == 0, out.output
    assert printed_before_run
    plan = out.output.split("Plan: ")[1].split("Tier 2 config")[0]
    assert plan.startswith("2 questions x role admin x 1 pass, cap $5.00")
    assert "gpt-5.4: gpt-5.4 (every node)" in plan
    assert str(tmp_path / "results" / "tier2") in plan and str(tmp_path / "t2.json") in plan


def test_outputs_default_to_the_project_folder(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    (project / ".env.demo").write_text("", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.delenv("ENV_FILE_PATH", raising=False)
    _FakeAPI.board = {**_board(), "database": CHINOOK}
    out = runner.invoke(app, ["--env", "demo", "benchmark", "--tier", "2", "--model", "gpt-5.4", "--max-cost", "5",
                              "--note", "slim prompts"])
    assert out.exit_code == 0, out.output
    assert (project / "benchmark_tier2.json").exists()
    written = sorted((project / "benchmarks" / "tier2" / "chinook").glob("*.json"))
    assert [p.name.rsplit("_", 1)[1] for p in written] == ["a.json", "b.json"]
    assert json.loads(written[0].read_text(encoding="utf-8"))["note"] == "slim prompts"


def test_benchmark_presets_lists_each_preset_with_the_model_on_each_node():
    out = runner.invoke(app, ["benchmark", "presets"])
    assert out.exit_code == 0, out.output
    lines = out.output.splitlines()
    assert "  gpt-5.4: gpt-5.4 (every node)" in lines
    assert ("  gpt-5.4-mini-helpers: gpt-5.4 (astplanner, refiner); "
            "gpt-5.4-mini (answersynthesizer, datasourceresolver, decomposer)") in lines
    assert ("  claude-planner: claude-opus-5 (astplanner, refiner); "
            "gpt-5.4 (answersynthesizer, datasourceresolver, decomposer)") in lines


def _record_file(root, name, body):
    path = root / "benchmarks" / "tier2" / "chinook" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema": 2, "kind": "tier2", "recorded_at": "2026-09-21T10:00:00Z", "engine_version": "0.1.2",
              "git_commit": "abcdef1234", "git_dirty": False, "note": body, "dataset": {"name": "g.yaml",
              "sha256": "0" * 64}, "database": CHINOOK, "config": {"name": name.rsplit("_", 1)[1][:-5],
              "models": {"astplanner": "openai:gpt-5.4"}}, "roles": ["admin"], "passes": 1,
              "metrics": {"accuracy": 0.9, "faithfulness": 1.0, "cost_per_question": 0.01,
                          "answerability_precision": 1.0, "answerability_recall": 1.0,
                          "tokens_per_question": {}, "latency_p50": 1.0, "latency_p95": 2.0, "determinism": None},
              "stopped": None, "partial": False}
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_publish_from_another_project_copies_new_records_and_reports_a_conflict(tmp_path):
    demo, repo = tmp_path / "demo", tmp_path / "repo"
    _record_file(demo, "2026-09-21_abcdef1_a.json", "new")
    _record_file(demo, "2026-09-21_abcdef1_b.json", "demo's")
    mine = _record_file(repo, "2026-09-21_abcdef1_b.json", "mine")
    readme = repo / "README.md"
    readme.write_text("x\n<!-- BENCHMARKS:START -->\nold\n<!-- BENCHMARKS:END -->\n", encoding="utf-8")
    args = ["benchmark", "publish", "--from", str(demo), "--results-dir", str(repo / "benchmarks"),
            "--history-path", str(repo / "docs" / "benchmarks.md"), "--readme-path", str(readme)]
    out = runner.invoke(app, args, env={"COLUMNS": "250"})
    assert out.exit_code == 1, out.output
    assert "Copied tier2/chinook/2026-09-21_abcdef1_a.json" in out.output
    assert "Conflict: tier2/chinook/2026-09-21_abcdef1_b.json" in out.output
    assert json.loads(mine.read_text(encoding="utf-8"))["note"] == "mine"
    assert (repo / "benchmarks" / "tier2" / "chinook" / "2026-09-21_abcdef1_a.json").exists()
    assert "| a | chinook |" in readme.read_text(encoding="utf-8")
