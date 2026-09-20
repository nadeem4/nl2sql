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
