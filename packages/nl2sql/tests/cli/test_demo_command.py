"""The `nl2sql demo` command: scaffolding, mode detection and server launch."""
import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stay_put(tmp_path, monkeypatch):
    """`demo_command` chdirs into the demo project; put the cwd back afterwards."""
    import os

    original = os.getcwd()
    yield
    os.chdir(original)


def test_demo_scaffolds_indexes_and_starts_replay_when_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    launched = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: launched.update(host=host, port=port))
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())
    monkeypatch.setattr("webbrowser.open", lambda url: launched.update(browser=url))

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--port", "8999"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "d" / "data" / "chinook.sqlite").exists()
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text())
    assert llm["default"]["base_url"].startswith("http://127.0.0.1:")
    assert launched == {"host": "127.0.0.1", "port": 8999}
    assert "replay mode" in result.output.lower()


def _replay_demo(tmp_path, monkeypatch, packaged=None):
    """Runs `nl2sql demo` with no key; returns (flattened output, the served app).

    ``packaged`` stands in for the recordings shipped inside the package, which
    today are none.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())
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


def test_replay_without_recordings_says_it_cannot_answer(tmp_path, monkeypatch):
    output, served_app = _replay_demo(tmp_path, monkeypatch)

    assert "Replay mode: no API key found, and replay mode has no recorded answers" in output
    assert "add an API key in the playground's Settings panel or pass --api-key" in output
    assert "recorded responses" not in output
    assert _meta(served_app)["recorded_questions"] == 0


def test_replay_reads_the_recordings_written_by_record(tmp_path, monkeypatch):
    """`--record` writes `<demo>/recordings.json`; replay has to read it back."""
    from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    (tmp_path / "d").mkdir()
    ReplayStore([Recording("DecomposerResponse", CHINOOK_QUESTIONS[0], {})]).save(
        tmp_path / "d" / "recordings.json"
    )

    output, served_app = _replay_demo(tmp_path, monkeypatch)

    assert f"1 of {len(CHINOOK_QUESTIONS)} guided questions answer from recorded responses" in output
    assert "no recorded answers" not in output
    assert _meta(served_app)["recorded_questions"] == 1


def test_replay_falls_back_to_the_packaged_recordings(tmp_path, monkeypatch):
    from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    packaged = ReplayStore([Recording("DecomposerResponse", q, {}) for q in CHINOOK_QUESTIONS[:2]])

    output, served_app = _replay_demo(tmp_path, monkeypatch, packaged=packaged)

    assert f"2 of {len(CHINOOK_QUESTIONS)} guided questions answer from recorded responses" in output
    assert _meta(served_app)["recorded_questions"] == 2


def test_demo_uses_live_mode_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text())
    assert llm["default"].get("base_url") in (None, "")
    assert "live mode" in result.output.lower()


def test_indexing_runs_from_inside_the_demo_directory(tmp_path, monkeypatch):
    """The schema store path is relative to the cwd, not to the project root.

    Indexing from anywhere else writes the snapshot where the engine -- and so
    ``GET /api/schema`` -- cannot find it, and the schema panel comes up empty.
    """
    import os

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    seen = {}
    monkeypatch.setattr(
        "nl2sql.cli.demo.manager.DemoManager.index_demo_data",
        lambda self: seen.update(cwd=os.getcwd()) or True,
    )
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())

    target = tmp_path / "d"
    result = runner.invoke(app, ["demo", "--dir", str(target), "--no-browser"])

    assert result.exit_code == 0, result.output
    assert seen["cwd"] == str(target.resolve())


def test_indexing_cannot_blank_the_real_api_key(tmp_path, monkeypatch):
    """`.env.demo` ships an empty OPENAI_API_KEY placeholder.

    Indexing loads it with `override=True`, so a real key in the environment was
    replaced by an empty string and live mode silently fell back to `ollama`
    with no key at all.
    """
    import os

    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def _blanking_index(self):
        os.environ["OPENAI_API_KEY"] = ""  # what load_dotenv(override=True) does
        return True

    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", _blanking_index)
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser"])

    assert result.exit_code == 0, result.output
    assert "using openai" in result.output.lower(), result.output
    assert os.environ["OPENAI_API_KEY"] == "sk-real"


def test_detect_llm_mode_reads_the_environment(monkeypatch):
    from nl2sql.cli.commands import demo

    monkeypatch.setattr(demo, "_ollama_reachable", lambda: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert demo.detect_llm_mode() == "replay"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    assert demo.detect_llm_mode() == "live"

    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(demo, "_ollama_reachable", lambda: True)
    assert demo.detect_llm_mode() == "live"


def test_record_without_a_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_record_needs_a_key_even_when_ollama_is_running(tmp_path, monkeypatch):
    """Ollama makes the mode `live`, but there is nothing to record through.

    `--record` proxies to an OpenAI-compatible upstream with a bearer token; a
    reachable Ollama gave neither, so recording started with an empty key and
    failed confusingly instead of saying what was missing.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: True)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--no-browser", "--record"])

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


class _StubEngine:
    """Stands in for `NL2SQL()`; the command only reads policies off it."""

    def __init__(self):
        self.context = _StubContext()

    def run_query(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise AssertionError("the stub engine does not run queries")


class _StubContext:
    def __init__(self):
        self.policies_cfg = _StubPolicies()
        self.schema_store = None
        self.ds_registry = None


class _StubPolicies:
    roles = {"admin": object(), "analyst": object(), "viewer": object()}
