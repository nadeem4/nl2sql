"""``nl2sql demo --record`` keeps only the questions whose run actually answered.

A real recording run printed "Recorded 20 of 20" although 10 runs had failed:
every model call is recorded as it passes through the proxy, so a run that
died in the aggregator still left a decomposer recording, and a question counts
as covered when it has one. Replay would then have served the half-run to
visitors.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from nl2sql.cli.commands import demo
from nl2sql.llm.replay import Recording, ReplayStore

OK, BROKEN = "How many customers are there?", "Which customers bought jazz tracks but never rock?"


class _Proxy:
    def stop(self):
        pass


class _Engine:
    """Answers OK, fails BROKEN the way the aggregator failure did."""

    def __init__(self, store):
        self.store = store

    def run_query(self, question, execute, user_context):
        # What the recording proxy would have stored while the run went through it.
        self.store.add(Recording("DecomposerResponse", question, {"sub_queries": []}))
        if question == BROKEN and "viewer" not in user_context.roles:
            return SimpleNamespace(status="error", final_answer=None, errors=[{
                "node": "engineaggregator",
                "message": "Post-combine operation references 'bought_rock', which the combined "
                           "result does not have. Available columns: customer."}])
        return SimpleNamespace(status="success", final_answer={"summary": "59"}, errors=[])


def test_a_failed_run_is_not_recorded_and_is_reported(tmp_path: pathlib.Path, capsys):
    store = ReplayStore([Recording("plain", None, "Keep the same plan.")])

    failed = demo._record_all(_Engine(store), [BROKEN, OK], _Proxy(), store, tmp_path / "recordings.json")

    assert failed == [BROKEN]
    saved = ReplayStore.load(tmp_path / "recordings.json")
    assert saved.covered([OK, BROKEN]) == [OK]
    # Recordings no question owns are kept.
    assert any(r.name == "plain" for r in saved.rules())
    out = " ".join(capsys.readouterr().out.split())
    assert "bought_rock" in out and "1 of 2" in out


@pytest.mark.parametrize("result", [
    SimpleNamespace(status="plan_only", final_answer={"summary": "x"}, errors=[]),
    SimpleNamespace(status="success", final_answer=None, errors=[]),
    SimpleNamespace(status="success", final_answer={"summary": "x"}, errors=[{"message": "boom"}]),
])
def test_success_means_rows_an_answer_and_no_error(result):
    assert demo.run_succeeded(result) is False


def test_a_run_with_rows_an_answer_and_no_error_succeeded():
    assert demo.run_succeeded(SimpleNamespace(status="success", final_answer={"summary": "59"}, errors=[]))


def test_record_exits_non_zero_when_any_question_failed(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from nl2sql.cli.main import app

    pytest.importorskip("fastapi")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "fake-record-test")
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", lambda *a, **k: SimpleNamespace(
        start=lambda: SimpleNamespace(base_url="http://127.0.0.1:9/v1", stop=lambda: None)))
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine",
                        lambda: SimpleNamespace(context=SimpleNamespace(
                            policies_cfg=SimpleNamespace(roles={"admin": None}))))
    monkeypatch.setattr("nl2sql.cli.commands.demo._record_all", lambda *args: [BROKEN])
    cwd = pathlib.Path.cwd()
    try:
        result = CliRunner().invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])
    finally:
        import os
        os.chdir(cwd)

    assert result.exit_code == 1, result.output
