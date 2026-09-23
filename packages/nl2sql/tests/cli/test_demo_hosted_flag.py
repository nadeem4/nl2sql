"""`nl2sql demo --hosted`: the public-demo server, and what it refuses.

Hosted mode is a different server, not a looser one: it holds no key of its
own, writes nothing a visitor asked for, and turns down ``--record`` and
``--api-key`` before it starts.
"""
import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.demo.playground.hosted import HOSTED_ENV, QUESTIONS_PER_MINUTE, QUESTIONS_PER_SESSION
from nl2sql.cli.main import app

runner = CliRunner()

FAKE_KEY = "-".join(["sk", "proj", "hostedcli" + "d" * 24 + "2a6b"])


@pytest.fixture(autouse=True)
def _stay_put():
    """`demo_command` chdirs into the demo project; put the cwd back afterwards."""
    import os

    original = os.getcwd()
    yield
    os.chdir(original)


@pytest.fixture(autouse=True)
def _no_keys_anywhere(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(HOSTED_ENV, raising=False)
    monkeypatch.delenv(QUESTIONS_PER_MINUTE, raising=False)
    monkeypatch.delenv(QUESTIONS_PER_SESSION, raising=False)


def _run(tmp_path, monkeypatch, *args):
    """Runs the command with scaffolding and indexing stubbed; returns (result, served app)."""
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())
    monkeypatch.setattr("webbrowser.open", lambda url: served.update(browser=url))
    served = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve",
                        lambda application, host, port: served.update(app=application, host=host))

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), *args])
    return result, served


def _meta(served):
    from fastapi.testclient import TestClient

    return TestClient(served["app"]).get("/api/meta").json()


def test_hosted_serves_a_playground_with_no_key_of_its_own(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("OPENAI_API_KEY", "the-owner-key-this-process-started-with")

    result, served = _run(tmp_path, monkeypatch, "--hosted", "--host", "0.0.0.0")

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Hosted mode: the server holds no API key" in output
    assert "6 a minute and 30 a session" in output
    # Whatever the host exported is gone, so nothing can spend the owner's key.
    assert not os.environ.get("OPENAI_API_KEY")
    # The config is pointed at a live endpoint; the request's key picks the provider.
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text(encoding="utf-8"))
    assert llm["default"].get("base_url") is None
    assert _meta(served)["hosted"] is True
    assert _meta(served)["mode"] == "hosted"


def test_hosted_does_not_open_a_browser(tmp_path, monkeypatch):
    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 0, result.output
    assert "browser" not in served


def test_the_environment_variable_turns_hosted_mode_on_for_a_container(tmp_path, monkeypatch):
    monkeypatch.setenv(HOSTED_ENV, "1")

    result, served = _run(tmp_path, monkeypatch, "--no-browser")

    assert result.exit_code == 0, result.output
    assert _meta(served)["hosted"] is True


def test_the_limits_can_be_set_for_the_container(tmp_path, monkeypatch):
    monkeypatch.setenv(QUESTIONS_PER_MINUTE, "3")
    monkeypatch.setenv(QUESTIONS_PER_SESSION, "12")

    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 0, result.output
    assert _meta(served)["limits"] == {"questions_per_minute": 3, "questions_per_session": 12}


def test_record_is_refused_in_hosted_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    result, served = _run(tmp_path, monkeypatch, "--hosted", "--record")

    assert result.exit_code == 1
    assert "--record is refused in hosted mode" in " ".join(result.output.split())
    assert served == {}


def test_a_server_side_key_is_refused_in_hosted_mode(tmp_path, monkeypatch):
    result, served = _run(tmp_path, monkeypatch, "--hosted", "--api-key", FAKE_KEY)

    assert result.exit_code == 1
    assert "--api-key is refused in hosted mode" in " ".join(result.output.split())
    assert FAKE_KEY not in result.output
    assert served == {}


def test_without_the_flag_nothing_changes(tmp_path, monkeypatch):
    result, served = _run(tmp_path, monkeypatch, "--no-browser")

    assert result.exit_code == 0, result.output
    assert "replay mode" in result.output.lower()
    assert _meta(served)["hosted"] is False


class _StubEngine:
    """Stands in for `NL2SQL()`; the command only reads policies off it."""

    def __init__(self):
        self.context = _StubContext()

    def list_datasources(self):
        return []


class _StubContext:
    def __init__(self):
        self.policies_cfg = _StubPolicies()
        self.schema_store = None
        self.ds_registry = None


class _StubPolicies:
    roles = {"admin": object(), "analyst": object(), "viewer": object()}
