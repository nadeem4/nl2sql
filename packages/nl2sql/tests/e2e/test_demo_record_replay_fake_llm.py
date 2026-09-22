"""`nl2sql demo --record`, then `nl2sql demo` with no key, answers from the recording.

The provider is a ``FakeLLMServer`` standing in for OpenAI, reached through the
real ``RecordingProxy``. No real key is used and nothing is spent.
"""
import shutil

import pytest
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS  # noqa: E402
from nl2sql.cli.main import app  # noqa: E402
from nl2sql.llm.replay import ReplayStore  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402

FAKE_KEY = "-".join(["sk", "proj", "e2e" + "r" * 30 + "0c1d"])
UNRECORDED = "How many tracks are longer than ten minutes?"
MISS_MESSAGE = "No recorded answer for this question. Add an API key to ask it live."


@pytest.mark.e2e
def test_record_then_replay_with_no_key(demo_project, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql.common.settings import reload_settings

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    monkeypatch.chdir(tmp_path)  # the command chdirs; this puts the cwd back
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    provider = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    monkeypatch.setattr("nl2sql.cli.commands.demo.UPSTREAMS", {"openai": provider.base_url})
    seen = {}

    def _serve(app_, host, port):
        # The replay server stops when serving ends, so ask while "serving".
        client = TestClient(app_, base_url="http://127.0.0.1:8765")
        seen["meta"] = client.get("/api/meta").json()
        seen["answered"] = client.post("/api/ask", json={"question": CHINOOK_QUESTIONS[0], "role": "admin"}).json()
        seen["missed"] = client.post("/api/ask", json={"question": UNRECORDED, "role": "admin"})

    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", _serve)
    runner = CliRunner()

    try:
        recorded = runner.invoke(app, ["demo", "--dir", str(project), "--no-browser", "--record"])
        provider.stop()  # replay must not need the provider
        monkeypatch.delenv("OPENAI_API_KEY")
        replayed = runner.invoke(app, ["demo", "--dir", str(project), "--no-browser"])
    finally:
        provider.stop()
        reload_settings()

    assert recorded.exit_code == 0, recorded.output
    assert set(ReplayStore.load(project / "recordings.json").covered(CHINOOK_QUESTIONS)) == set(CHINOOK_QUESTIONS)

    assert replayed.exit_code == 0, replayed.output
    total = len(CHINOOK_QUESTIONS)
    assert f"{total} of {total} guided questions answer from recorded responses" in " ".join(
        replayed.output.split()
    )
    meta, answered, missed = seen["meta"], seen["answered"], seen["missed"]
    assert meta["mode"] == "replay" and meta["recorded_questions"] == total

    assert answered["replay_miss"] is False
    assert answered["status"] == "success", answered["errors"]
    assert "COUNT(" in answered["sub_queries"][0]["sql"]

    assert missed.json()["replay_miss"] is True
    assert [e["message"] for e in missed.json()["errors"]] == [MISS_MESSAGE]
    assert "fake llm" not in missed.text and "ORCHESTRATOR_CRASH" not in missed.text
