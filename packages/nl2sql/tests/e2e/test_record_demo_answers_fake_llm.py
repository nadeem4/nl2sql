"""``scripts/record_demo_answers.py``, then a keyless hosted visitor is answered from what it wrote.

The upstream is a ``FakeLLMServer`` answering Anthropic's Messages API, reached
through the real ``RecordingProxy`` on the Anthropic wire, so the whole chain --
record with Claude, ship the file, replay on the hosted demo with no key -- runs
with no real key and nothing spent.
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from nl2sql.cli.demo.datasets import CHINOOK_QUESTIONS, DEMO_QUESTIONS  # noqa: E402
from nl2sql.llm.replay import ReplayStore  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_demo_answers.py"
FAKE_CLAUDE = "-".join(["sk", "ant", "api03", "recorde2e" + "g" * 28 + "5e3d"])
UNRECORDED = "How many tracks are longer than ten minutes?"


def _script():
    spec = importlib.util.spec_from_file_location("record_demo_answers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.e2e
def test_record_with_claude_then_replay_to_a_keyless_hosted_visitor(demo_project, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.llm_config import point_llm_config_at
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.cli.demo.playground.hosted import Hosted, Replay
    from nl2sql.common.settings import reload_settings

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    out = tmp_path / "chinook.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    claude = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    monkeypatch.setattr("nl2sql.cli.commands.demo.ANTHROPIC_UPSTREAM", claude.anthropic_base_url)

    try:
        status = _script().main(["--dir", str(project), "--out", str(out),
                                 "--env-file", str(tmp_path / "no.env")])
    finally:
        claude.stop()
        reload_settings()

    # Recorded on Anthropic's wire, with the visitor-free key, for every guided question.
    assert status == 0
    assert claude.calls and all(call["mode"] == "anthropic" and call["api_key"] == FAKE_CLAUDE
                                for call in claude.calls)
    store = ReplayStore.load(out)
    assert set(store.covered(DEMO_QUESTIONS)) == set(DEMO_QUESTIONS)

    # The hosted demo, as `nl2sql demo --hosted` leaves it: no key anywhere.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    point_llm_config_at(project, None, provider="openai")
    monkeypatch.chdir(project)
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    monkeypatch.setenv("PLAN_CACHE_ENABLED", "false")
    reload_settings()
    replay = FakeLLMServer(store.rules()).start()
    try:
        app = build_app(NL2SQL(), questions=list(DEMO_QUESTIONS), roles=["admin"], mode="hosted",
                        dataset="chinook", project_dir=project, host="0.0.0.0",
                        hosted=Hosted(enabled=True, replay=Replay(replay.base_url, store.covered(DEMO_QUESTIONS))))
        client = TestClient(app, base_url="http://demo.example:7860")
        answered = client.post("/api/ask", json={"question": CHINOOK_QUESTIONS[0], "role": "admin"}).json()
        missed = client.post("/api/ask", json={"question": UNRECORDED, "role": "admin"}).json()
    finally:
        replay.stop()
        reload_settings()

    assert answered["recorded"] is True and answered["replay_miss"] is False
    assert answered["status"] == "success", answered["errors"]
    assert "COUNT(" in answered["sub_queries"][0]["sql"]
    assert missed["replay_miss"] is True and missed["recorded"] is False
