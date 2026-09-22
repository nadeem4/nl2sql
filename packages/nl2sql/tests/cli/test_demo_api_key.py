"""`nl2sql demo --api-key`: live mode from the command line, persisted.

Precedence, highest first: ``--api-key``, the process environment, the demo
project's ``.env.demo``, a reachable Ollama, replay.
"""
import os
import re

import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.main import app

runner = CliRunner()

# Built at run time so no secret scanner mistakes a fixture for a leaked key.
FAKE_KEY = "-".join(["sk", "test", "not", "a", "real", "key", "1234"])
FAKE_OPENROUTER_KEY = "-".join(["sk", "or", "v1", "test", "not", "a", "real", "key", "5678"])
FAKE_ANTHROPIC_KEY = "-".join(["sk", "ant", "api03", "test", "not", "a", "real", "key", "9012"])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """`demo_command` chdirs and writes ENV; put both back afterwards."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # monkeypatch records ENV here, so the command's own os.environ write is
    # rolled back at teardown.
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", lambda self: True)
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve", lambda app, host, port: None)
    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", lambda: _StubEngine())
    original = os.getcwd()
    yield
    os.chdir(original)


def _run(*args):
    return runner.invoke(app, ["demo", "--no-browser", *args])


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strips the colour rich adds when it believes it has a terminal.

    CI has one and a local run may not. Rich's repr highlighter colours numbers
    inside a word, so `sk-...1234` and a key ending in digits both arrive split
    across escape sequences -- which would quietly turn "the key is not in the
    output" into a test that cannot fail.
    """
    return _ANSI.sub("", text)


def test_api_key_selects_live_mode_with_nothing_in_the_environment(tmp_path):
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    assert "live mode" in _plain(result.output).lower(), result.output
    assert "using openai" in _plain(result.output).lower(), result.output
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text())
    # No base_url means the engine talks to the real provider, not a local
    # replay or recording server.
    assert llm["default"].get("base_url") in (None, "")


def test_api_key_is_written_into_env_demo(tmp_path):
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    env_demo = (tmp_path / "d" / ".env.demo").read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={FAKE_KEY}" in env_demo
    # The shipped empty placeholder must not survive below the real value:
    # indexing loads this file with override=True and would blank the key.
    assert "OPENAI_API_KEY=\n" not in env_demo


def test_a_later_run_in_that_directory_is_live_without_any_environment(tmp_path):
    directory = tmp_path / "d"
    first = _run("--dir", str(directory), "--api-key", FAKE_KEY)
    assert first.exit_code == 0, first.output

    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("OPENROUTER_API_KEY", None)

    second = _run("--dir", str(directory))

    assert second.exit_code == 0, second.output
    assert "live mode" in _plain(second.output).lower(), second.output
    assert "using openai" in _plain(second.output).lower(), second.output
    assert os.environ.get("OPENAI_API_KEY") == FAKE_KEY


def test_an_openrouter_shaped_key_selects_openrouter(tmp_path):
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_OPENROUTER_KEY)

    assert result.exit_code == 0, result.output
    assert "using openrouter" in _plain(result.output).lower(), result.output
    env_demo = (tmp_path / "d" / ".env.demo").read_text(encoding="utf-8")
    assert f"OPENROUTER_API_KEY={FAKE_OPENROUTER_KEY}" in env_demo


def test_api_key_beats_a_key_already_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-from-the-environment")

    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    assert os.environ["OPENAI_API_KEY"] == FAKE_KEY
    env_demo = (tmp_path / "d" / ".env.demo").read_text(encoding="utf-8")
    assert FAKE_KEY in env_demo
    assert "sk-test-from-the-environment" not in env_demo


def test_the_environment_beats_a_key_stored_in_env_demo(tmp_path, monkeypatch):
    directory = tmp_path / "d"
    assert _run("--dir", str(directory), "--api-key", FAKE_KEY).exit_code == 0

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-from-the-environment")
    result = _run("--dir", str(directory))

    assert result.exit_code == 0, result.output
    assert os.environ["OPENAI_API_KEY"] == "sk-test-from-the-environment"


def test_api_key_satisfies_record(tmp_path, monkeypatch):
    recorded = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", _StubProxy)
    monkeypatch.setattr(
        "nl2sql.cli.commands.demo._record_all",
        lambda engine, questions, proxy, store, store_path: recorded.update(done=True),
    )

    result = _run("--dir", str(tmp_path / "d"), "--record", "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    assert recorded == {"done": True}
    assert _StubProxy.last_key == FAKE_KEY
    assert _StubProxy.last_upstream == "https://api.openai.com/v1"


def test_record_with_an_openrouter_key_proxies_to_openrouter(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", _StubProxy)
    monkeypatch.setattr(
        "nl2sql.cli.commands.demo._record_all",
        lambda engine, questions, proxy, store, store_path: None,
    )

    result = _run("--dir", str(tmp_path / "d"), "--record", "--api-key", FAKE_OPENROUTER_KEY)

    assert result.exit_code == 0, result.output
    assert _StubProxy.last_upstream == "https://openrouter.ai/api/v1"
    assert _StubProxy.last_key == FAKE_OPENROUTER_KEY


def test_the_key_is_never_echoed(tmp_path):
    """Console output must never carry the key, only a masked form."""
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    plain = _plain(result.output)
    assert FAKE_KEY not in plain
    assert FAKE_KEY not in _plain(result.stdout)
    # Rich wraps long lines, so a key could also be split across them.
    assert FAKE_KEY not in "".join(plain.split())
    # The generated LLM config points at the variable, it does not inline it.
    assert FAKE_KEY not in (tmp_path / "d" / "configs" / "llm.demo.yaml").read_text()
    assert "sk-...1234" in plain


def test_the_key_is_not_echoed_when_the_command_fails(tmp_path, monkeypatch):
    """`handle_cli_errors` prints a full traceback; the key must not be in it."""

    def _boom():
        raise RuntimeError("engine construction failed")

    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", _boom)

    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 1
    plain = _plain(result.output)
    assert FAKE_KEY not in plain
    assert FAKE_KEY not in "".join(plain.split())


def test_an_anthropic_key_selects_claude(tmp_path):
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_ANTHROPIC_KEY)

    assert result.exit_code == 0, result.output
    assert "using anthropic" in _plain(result.output).lower(), result.output
    env_demo = (tmp_path / "d" / ".env.demo").read_text(encoding="utf-8")
    assert f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC_KEY}" in env_demo
    assert "OPENAI_API_KEY=" not in env_demo
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text())
    # The scaffolded gpt model means nothing to Anthropic, and Claude 5 models
    # reject a temperature, so both follow the provider.
    assert llm["default"]["provider"] == "anthropic"
    assert llm["default"]["model"] == "claude-opus-5"
    assert llm["default"]["temperature"] is None
    assert llm["default"]["api_key"] == "${env:ANTHROPIC_API_KEY}"


def test_an_anthropic_key_in_the_environment_selects_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)

    result = _run("--dir", str(tmp_path / "d"))

    assert result.exit_code == 0, result.output
    assert "using anthropic" in _plain(result.output).lower(), result.output


def test_record_refuses_an_anthropic_key(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.cli.commands.demo.RecordingProxy", _StubProxy)

    result = _run("--dir", str(tmp_path / "d"), "--record", "--api-key", FAKE_ANTHROPIC_KEY)

    assert result.exit_code == 1
    assert "OpenAI" in _plain(result.output)


def test_help_states_the_precedence_and_the_argv_caveat():
    result = runner.invoke(app, ["demo", "--help"])

    assert result.exit_code == 0
    # Rich colours the help when it thinks it has a terminal -- CI does, a local
    # run may not -- and an option name is split across colour spans
    # (`ESC[1;36m-ESC[0mESC[1;36m-api-key`). Strip the escapes first, then the
    # box-drawing border, then the wrapping, before matching on phrases.
    text = " ".join(re.sub(r"[^\x20-\x7e]", " ", _plain(result.output)).split())
    assert "--api-key" in text
    assert ".env.demo" in text
    assert "OPENROUTER_API_KEY" in text
    assert "Ollama" in text
    assert "replay" in text
    assert "shell history" in text


def _served_app(monkeypatch):
    """Captures the app `demo_command` would serve."""
    served = {}
    monkeypatch.setattr("nl2sql.cli.commands.demo._serve",
                        lambda app, host, port: served.update(app=app, host=host))
    return served


def _settings(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://127.0.0.1:8765").get("/api/settings").json()


def test_an_openrouter_key_is_read_from_the_openrouter_variable(tmp_path):
    """The scaffolded config reads ${env:OPENAI_API_KEY}; OpenRouter needs its own."""
    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_OPENROUTER_KEY)

    assert result.exit_code == 0, result.output
    llm = yaml.safe_load((tmp_path / "d" / "configs" / "llm.demo.yaml").read_text())
    assert llm["default"]["api_key"] == "${env:OPENROUTER_API_KEY}"


def test_the_api_key_flag_path_is_unchanged_by_the_settings_panel(tmp_path, monkeypatch):
    served = _served_app(monkeypatch)

    result = _run("--dir", str(tmp_path / "d"), "--api-key", FAKE_KEY)

    assert result.exit_code == 0, result.output
    assert "live mode" in _plain(result.output).lower()
    assert f"OPENAI_API_KEY={FAKE_KEY}" in (tmp_path / "d" / ".env.demo").read_text(encoding="utf-8")
    body = _settings(served["app"])
    assert body["available"] is True and body["mode"] == "live"
    assert body["key"] == {"masked": "sk-...1234", "env_var": "OPENAI_API_KEY"}
    assert FAKE_KEY not in str(body)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_settings_are_off_on_a_public_host_unless_allowed(tmp_path, monkeypatch, host):
    served = _served_app(monkeypatch)

    assert _run("--dir", str(tmp_path / "d"), "--host", host).exit_code == 0
    refused = _settings(served["app"])
    assert _run("--dir", str(tmp_path / "d"), "--host", host, "--allow-settings").exit_code == 0
    allowed = _settings(served["app"])

    assert refused["available"] is False and "--allow-settings" in refused["reason"]
    assert allowed["available"] is True


def test_settings_are_on_for_the_default_localhost_bind(tmp_path, monkeypatch):
    served = _served_app(monkeypatch)

    assert _run("--dir", str(tmp_path / "d")).exit_code == 0

    assert served["host"] == "127.0.0.1"
    assert _settings(served["app"])["available"] is True


def test_replay_points_per_node_models_at_the_replay_server_too(tmp_path):
    """A node chosen in the panel must not reach the real provider in replay."""
    directory = tmp_path / "d"
    assert _run("--dir", str(directory), "--api-key", FAKE_KEY).exit_code == 0
    path = directory / "configs" / "llm.demo.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["agents"] = {"astplanner": {"provider": "openai", "model": "gpt-4.1", "temperature": 0.0,
                                    "api_key": "${env:OPENAI_API_KEY}", "name": "astplanner"}}
    path.write_text(yaml.safe_dump(cfg))
    (directory / ".env.demo").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    os.environ.pop("OPENAI_API_KEY", None)

    result = _run("--dir", str(directory))

    assert "replay mode" in _plain(result.output).lower(), result.output
    llm = yaml.safe_load(path.read_text())
    assert llm["agents"]["astplanner"]["base_url"] == llm["default"]["base_url"]
    assert llm["agents"]["astplanner"]["base_url"].startswith("http://127.0.0.1:")


class _StubProxy:
    """Stands in for `RecordingProxy`; records what it was asked to proxy."""

    last_key = None
    last_upstream = None

    def __init__(self, upstream, key, store, **kwargs):
        _StubProxy.last_upstream = upstream
        _StubProxy.last_key = key
        self.base_url = "http://127.0.0.1:9/v1"

    def start(self):
        return self

    def stop(self):
        return None


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
