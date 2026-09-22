"""`nl2sql benchmark`: the tier switch, the JSON report and the exit code.

The benchmark API is replaced, so no context or pipeline is built here; the
real tier 1 run is ``tests/e2e/test_benchmark_tier1.py``.
"""
import json

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.evaluation.benchmark_runner import BenchmarkResult
from nl2sql.evaluation.evaluator import ModelEvaluator

runner = CliRunner()


def _result(*statuses):
    results = [{"id": f"q{i}", "question": "?", "role": "admin", "expected": "allowed", "status": s,
                "reason": "" if s == "pass" else "boom", "sql": "", "rows": 1, "gold_rows": 1}
               for i, s in enumerate(statuses)]
    return BenchmarkResult(results=results, metrics=ModelEvaluator.summarize(results))


class _FakeAPI:
    result = None
    configs = []

    def run_tier1(self, config):
        _FakeAPI.configs.append(config)
        return _FakeAPI.result


def _run(monkeypatch, tmp_path, *statuses):
    _FakeAPI.result = _result(*statuses)
    monkeypatch.setattr("nl2sql.cli.commands.benchmark.BenchmarkAPI", _FakeAPI)
    report = tmp_path / "report.json"
    out = runner.invoke(app, ["benchmark", "--tier", "1", "--export-path", str(report), "--role", "admin"])
    return out, report


def test_tier1_writes_the_report_and_exits_zero_when_everything_passes(monkeypatch, tmp_path):
    out, report = _run(monkeypatch, tmp_path, "pass", "skip")
    assert out.exit_code == 0, out.output
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body["tier"] == 1
    assert body["configs"]["tier1"]["summary"]["total"] == {"pass": 1, "fail": 0, "skip": 1, "xfail": 0}
    assert _FakeAPI.configs[-1].roles == ["admin"]
    assert "q0" in out.output and "PASS" in out.output


def test_tier1_exits_one_on_any_failure_and_still_writes_the_report(monkeypatch, tmp_path):
    out, report = _run(monkeypatch, tmp_path, "pass", "fail")
    assert out.exit_code == 1
    assert json.loads(report.read_text(encoding="utf-8"))["configs"]["tier1"]["summary"]["total"]["fail"] == 1
    assert "FAIL" in out.output


def test_an_unknown_tier_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("nl2sql.cli.commands.benchmark.BenchmarkAPI", _FakeAPI)
    out = runner.invoke(app, ["benchmark", "--tier", "7", "--export-path", str(tmp_path / "r.json")])
    assert out.exit_code == 2
    assert not (tmp_path / "r.json").exists()
