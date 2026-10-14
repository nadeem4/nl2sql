"""`nl2sql demo --hosted`: the public-demo server, and what it refuses.

Hosted mode is a different server, not a looser one: it holds no key of its
own, writes nothing a visitor asked for, and turns down ``--record`` and
``--api-key`` before it starts.
"""
import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from nl2sql.cli.main import app

runner = CliRunner()

FAKE_KEY = "-".join(["sk", "proj", "hostedcli" + "d" * 24 + "2a6b"])


@pytest.fixture(autouse=True)
def _no_keys_anywhere(monkeypatch, demo_engines):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _run(tmp_path, monkeypatch, *args):
    """Runs the command with the engine stubbed (``demo_engines``); returns (result, served app)."""
    monkeypatch.setattr("nl2sql.cli.commands.demo._ollama_reachable", lambda: False)
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
    result, served = _run(tmp_path, monkeypatch, "--hosted", "--host", "0.0.0.0")

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Hosted mode: the server holds no API key" in output
    assert "6 a minute and 30 a session" in output
    # The config is pointed at a live endpoint; the request's key picks the provider.
    llm = yaml.safe_load((tmp_path / "d" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm.get("base_url") is None and llm.get("api_key") is None
    assert _meta(served)["hosted"] is True
    assert _meta(served)["mode"] == "hosted"


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"])
def test_hosted_refuses_to_start_with_a_provider_key_in_its_environment(tmp_path, monkeypatch, variable):
    """Every visitor would spend a key the server holds, and the demo never writes the environment to drop it."""
    import os

    monkeypatch.setenv(variable, FAKE_KEY)

    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert f"{variable} is set" in output and "Unset it" in output
    assert FAKE_KEY not in output
    assert served == {}
    assert os.environ[variable] == FAKE_KEY


def test_hosted_serves_the_packaged_recordings_to_a_visitor_without_a_key(tmp_path, monkeypatch):
    from nl2sql.datasets.demo import CHINOOK_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    shipped = tmp_path / "shipped"
    ReplayStore([Recording("DecomposerResponse", q, {}) for q in CHINOOK_QUESTIONS[:2]]).save(
        shipped / "chinook.json")
    monkeypatch.setattr("nl2sql.cli.commands.demo.RECORDINGS", shipped)

    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 0, result.output
    meta = _meta(served)
    assert meta["recorded"] == CHINOOK_QUESTIONS[:2]
    assert meta["recorded_questions"] == 2
    assert "2 guided questions answer from recordings without a key" in " ".join(result.output.split())
    # The replay server is the process's own, on loopback, and is not a key.
    replay = served["app"].state.hosted.replay
    assert replay.base_url.startswith("http://127.0.0.1:")


def test_hosted_without_recordings_asks_every_keyless_visitor_for_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.cli.commands.demo.RECORDINGS", tmp_path / "none")

    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 0, result.output
    assert _meta(served)["recorded"] == []
    assert served["app"].state.hosted.replay is None


def test_hosted_does_not_open_a_browser(tmp_path, monkeypatch):
    result, served = _run(tmp_path, monkeypatch, "--hosted")

    assert result.exit_code == 0, result.output
    assert "browser" not in served


def test_the_old_environment_variables_do_nothing(tmp_path, monkeypatch):
    """``NL2SQL_DEMO_HOSTED`` and the limit variables are gone: the flags say it."""
    monkeypatch.setenv("NL2SQL_DEMO_HOSTED", "1")
    monkeypatch.setenv("NL2SQL_DEMO_QUESTIONS_PER_MINUTE", "3")

    result, served = _run(tmp_path, monkeypatch, "--no-browser")

    assert result.exit_code == 0, result.output
    assert _meta(served)["hosted"] is False


def test_the_limits_are_flags(tmp_path, monkeypatch):
    result, served = _run(tmp_path, monkeypatch, "--hosted", "--questions-per-minute", "3",
                          "--questions-per-session", "12")

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
