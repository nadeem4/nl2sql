"""The settings panel switches a real engine from replay to live with no restart.

"Live" here is a second ``FakeLLMServer`` standing in for the provider, reached
the way a real one would be: the saved key, no ``base_url`` in the config, and
the OpenAI client's own endpoint variable pointing at the fake. No real key is
used and nothing is spent.
"""
import shutil

import pytest

pytest.importorskip("fastapi")

from nl2sql.cli.commands.demo import _point_llm_config_at  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402

FAKE_KEY = "-".join(["sk", "proj", "e2e" + "z" * 30 + "7d3e"])


@pytest.mark.e2e
def test_a_key_saved_in_the_panel_turns_replay_into_live(demo_project, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.common.settings import reload_settings

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    replay = FakeLLMServer([]).start()  # records nothing: every question misses
    live = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    _point_llm_config_at(project, replay.base_url)

    monkeypatch.chdir(project)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    monkeypatch.setenv("TRACE_MODE", "always")
    monkeypatch.setenv("OPENAI_API_KEY", "replay")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Where a client with no configured base_url goes: the stand-in provider.
    monkeypatch.setenv("OPENAI_API_BASE", live.base_url)
    monkeypatch.setenv("OPENAI_BASE_URL", live.base_url)
    reload_settings()

    try:
        engine = NL2SQL()
        app = build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook",
                        project_dir=project, host="127.0.0.1")
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        question = {"question": "How many customers are there?", "role": "admin"}

        before = client.post("/api/ask", json=question).json()
        live_calls_before = len(live.calls)
        saved = client.post("/api/settings/key", json={"api_key": FAKE_KEY})
        meta = client.get("/api/meta").json()
        after = client.post("/api/ask", json=question).json()
        trace = client.get(f"/api/trace/{after['trace_id']}")
    finally:
        replay.stop()
        live.stop()
        reload_settings()

    assert before["replay_miss"] is True
    assert replay.calls and live_calls_before == 0

    assert saved.status_code == 200, saved.text
    assert meta["mode"] == "live"

    assert after["status"] == "success", after["errors"]
    assert "COUNT(" in after["sub_queries"][0]["sql"]
    assert live.calls and all(c["authorization"] == f"Bearer {FAKE_KEY}" for c in live.calls)

    # Write-only: not in the answer, the settings read, or the run's trace.
    assert trace.status_code == 200
    for text in (saved.text, str(meta), str(after), trace.text):
        assert FAKE_KEY not in text
    assert saved.json()["key"]["masked"] == "sk-...7d3e"
    assert f"OPENAI_API_KEY={FAKE_KEY}" in (project / ".env.demo").read_text(encoding="utf-8")
