"""The `nl2sql demo` command: `init --demo`, mode detection, the engine check and server launch."""
import os
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.main import app

runner = CliRunner()

KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)


def _llm(tmp_path):
    return yaml.safe_load((tmp_path / "d" / "llm.yaml").read_text(encoding="utf-8"))


def test_demo_writes_the_project_with_init_demo_and_starts_replay_when_no_key(tmp_path, monkeypatch, demo_engines):
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    launched = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: launched.update(host=host, port=port))
    monkeypatch.setattr("webbrowser.open", lambda url: launched.update(browser=url))

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--port", "8999"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "d" / "nl2sql.yaml").exists() and (tmp_path / "d" / "data" / "chinook.sqlite").exists()
    llm = _llm(tmp_path)
    # Pointed at the local replay server, with the placeholder it takes: no key in the environment.
    assert llm["base_url"].startswith("http://127.0.0.1:") and llm["api_key"] == "replay"
    assert "OPENAI_API_KEY" not in os.environ
    assert launched == {"host": "127.0.0.1", "port": 8999}
    assert "replay mode" in result.output.lower()
    [engine] = demo_engines
    assert engine.config_path == (tmp_path / "d" / "nl2sql.yaml").resolve()
    assert engine.config.llm.base_url == llm["base_url"]


def _replay_demo(tmp_path, monkeypatch, packaged=None):
    """Runs `nl2sql demo` with no key; returns (flattened output, the served app).

    ``packaged`` stands in for the recordings shipped inside the package, which
    today are none.
    """
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    served = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: served.update(app=app))
    shipped = tmp_path / "packaged"
    shipped.mkdir()
    if packaged is not None:
        packaged.save(shipped / "chinook.json")
    monkeypatch.setattr("nl2sql.cli.commands.demo.RECORDINGS", shipped)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    return " ".join(result.output.split()), served["app"]


def _meta(served_app):
    from fastapi.testclient import TestClient

    return TestClient(served_app).get("/api/meta").json()


def test_replay_without_recordings_says_it_cannot_answer(tmp_path, monkeypatch, demo_engines):
    output, served_app = _replay_demo(tmp_path, monkeypatch)

    assert "Replay mode: no API key found, and replay mode has no recorded answers" in output
    assert "add an API key in the playground's Settings panel or pass --api-key" in output
    assert "recorded responses" not in output
    assert _meta(served_app)["recorded_questions"] == 0


def test_replay_reads_the_recordings_written_by_record(tmp_path, monkeypatch, demo_engines):
    """`--record` writes `<demo>/recordings.json`; replay has to read it back."""
    from nl2sql.datasets.demo import CHINOOK_QUESTIONS, DEMO_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    (tmp_path / "d").mkdir()
    ReplayStore([Recording("DecomposerResponse", CHINOOK_QUESTIONS[0], {})]).save(
        tmp_path / "d" / "recordings.json"
    )

    output, served_app = _replay_demo(tmp_path, monkeypatch)

    assert f"1 of {len(DEMO_QUESTIONS)} guided questions answer from recorded responses" in output
    assert "no recorded answers" not in output
    assert _meta(served_app)["recorded_questions"] == 1


def test_replay_streams_a_recorded_answer_at_reading_pace(tmp_path, monkeypatch, demo_engines):
    """A streamed recorded answer arrives a word at a time, not all at once."""
    from nl2sql.cli.commands.demo import REPLAY_STREAM_DELAY
    from nl2sql.testing.fake_llm import FakeLLMServer

    servers = []

    class Recorded(FakeLLMServer):
        def start(self):
            servers.append(self)
            return super().start()

    monkeypatch.setattr("nl2sql.cli.commands.demo.FakeLLMServer", Recorded)
    _replay_demo(tmp_path, monkeypatch)

    assert servers and all(s.stream_delay == REPLAY_STREAM_DELAY > 0 for s in servers)


def test_replay_falls_back_to_the_packaged_recordings(tmp_path, monkeypatch, demo_engines):
    from nl2sql.datasets.demo import CHINOOK_QUESTIONS, DEMO_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    packaged = ReplayStore([Recording("DecomposerResponse", q, {}) for q in CHINOOK_QUESTIONS[:2]])

    output, served_app = _replay_demo(tmp_path, monkeypatch, packaged=packaged)

    assert f"2 of {len(DEMO_QUESTIONS)} guided questions answer from recorded responses" in output
    assert _meta(served_app)["recorded_questions"] == 2


def test_demo_uses_live_mode_when_key_present(tmp_path, monkeypatch, demo_engines):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    llm = _llm(tmp_path)
    assert llm.get("base_url") is None and llm.get("api_key") is None
    assert "live mode" in result.output.lower()


def test_the_env_file_next_to_nl2sql_yaml_is_loaded_and_never_overrides(tmp_path, monkeypatch, demo_engines):
    """A key saved in the demo's .env makes it live; a key already exported wins over the file."""
    from nl2sql.cli.commands.demo import prepare_project

    prepare_project(tmp_path / "d")
    (tmp_path / "d" / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-from-file\nOPENAI_API_KEY=sk-from-file\n",
                                         encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    assert "using openai" in result.output.lower(), result.output
    assert os.environ["OPENAI_API_KEY"] == "sk-real"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-file"


def test_a_missing_index_is_built_before_the_playground_starts(tmp_path, monkeypatch, demo_engines):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    indexed = []
    monkeypatch.setattr("nl2sql.cli.common.indexing.run_indexing", lambda engine, **kw: indexed.append(engine))

    class Unindexed(demo_engines.cls):
        index = "missing"

    demo_engines.cls = Unindexed

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    assert indexed == list(demo_engines)
    assert "No vector index yet" in result.output


def test_detect_llm_mode_reads_the_environment(monkeypatch):
    from nl2sql.cli.commands import demo

    monkeypatch.setattr(demo, "_ollama_reachable", lambda: False)
    assert demo.detect_llm_mode() == "replay"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    assert demo.detect_llm_mode() == "live"

    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(demo, "_ollama_reachable", lambda: True)
    assert demo.detect_llm_mode() == "live"


def test_record_without_a_key_is_refused(tmp_path, monkeypatch, demo_engines):
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert not demo_engines


def test_record_needs_a_key_even_when_ollama_is_running(tmp_path, monkeypatch, demo_engines):
    """Ollama makes the mode `live`, but there is nothing to record through.

    `--record` proxies to an OpenAI-compatible upstream with a bearer token; a
    reachable Ollama gave neither, so recording started with an empty key and
    failed confusingly instead of saying what was missing.
    """
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: True)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


class _Proxy:
    base_url = "http://127.0.0.1:9/v1"

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def stop(self):
        pass


def test_record_turns_the_plan_cache_off_so_every_planner_answer_is_recorded(tmp_path, monkeypatch, demo_engines):
    """A cached plan makes no planner call, so replay would have nothing to answer with."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "fake-record-test")
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", _Proxy)

    class Recorder(demo_engines.cls):
        def run_query(self, *args, **kwargs):
            return SimpleNamespace(status="success", final_answer={"summary": "ok"}, errors=[])

    demo_engines.cls = Recorder

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 0, result.output
    [engine] = demo_engines
    assert engine.plan_cache is False
    # The config points at the recording proxy with its placeholder key; the real key stays in the proxy.
    llm = _llm(tmp_path)
    assert llm["base_url"] == _Proxy.base_url and llm["api_key"] == "proxy"


def test_record_stops_when_indexing_fails(tmp_path, monkeypatch, demo_engines):
    """A recording made against no index records the wrong answers, so it stops."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "fake-record-test")
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", _Proxy)

    def _failing_index(engine, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr("nl2sql.cli.common.indexing.run_indexing", _failing_index)

    class Unindexed(demo_engines.cls):
        index = "missing"

        def run_query(self, *args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("recorded against no index")

    demo_engines.cls = Unindexed

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 1, result.output
    assert "Nothing was recorded" in " ".join(result.output.split())
