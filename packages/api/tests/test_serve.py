"""``nl2sql-api serve``: one worker, and D205's refusal to bind a non-loopback host with ``api.auth: none``."""
from pathlib import Path

import pytest

from nl2sql.testing.config import make_config
from nl2sql_api import serve


@pytest.fixture
def launched(monkeypatch):
    """What ``uvicorn.run`` was asked to do; nothing is served."""
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    return calls


def _config(tmp_path, **api):
    make_config(tmp_path, api=api)
    return str(tmp_path / "nl2sql.yaml")


def test_serve_binds_loopback_on_one_worker(tmp_path, launched):
    serve.main(["serve", "--config", _config(tmp_path), "--port", "9001"])
    [call] = launched
    assert call["host"] == "127.0.0.1" and call["port"] == 9001
    assert call.get("workers", 1) == 1 and "reload" not in call


def test_serve_refuses_auth_none_off_loopback(tmp_path, launched):
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--config", _config(tmp_path), "--host", "0.0.0.0"])
    assert "api.auth" in str(stop.value)
    assert launched == []


def test_serve_allows_a_public_host_with_keys(tmp_path, launched):
    serve.main(["serve", "--config", _config(tmp_path, auth="api_keys"), "--host", "0.0.0.0"])
    assert launched[0]["host"] == "0.0.0.0"


def test_serve_reports_a_bad_config_without_a_traceback(tmp_path, launched):
    (tmp_path / "nl2sql.yaml").write_text("version: 2\nnope: 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--config", str(tmp_path / "nl2sql.yaml")])
    assert "nope" in str(stop.value)
    assert launched == []


# ---- modes (D229) and the demo (A4b)

@pytest.fixture
def served(monkeypatch):
    """``serve.main`` with uvicorn, ``create_app`` and the demo preparation recorded; no environment key, no env mode."""
    seen = {"demo": [], "env_files": []}
    monkeypatch.delenv("NL2SQL_MODE", raising=False)
    monkeypatch.setattr(serve, "provider_keys_in_environment", lambda: {})
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: seen.update(app=app, run=kwargs))
    monkeypatch.setattr(serve, "create_app", lambda path=None, **kwargs: seen.update(path=path, **kwargs) or "app")
    monkeypatch.setattr(serve, "load_env_file", lambda path=None: seen["env_files"].append(path))

    def prepare(folder, *, provider=None):
        seen["demo"].append((str(folder), provider))
        return str(seen["config"])

    monkeypatch.setattr(serve, "prepare_demo", prepare)
    return seen


def _project(tmp_path, seen=None, **sections):
    sections.setdefault("policies", {"roles": {"admin": {"description": "role", "allow": ["*"]}, "viewer": {"description": "role", "allow": ["*"]}}})
    config = make_config(tmp_path, **sections)
    path = tmp_path / "nl2sql.yaml"
    if seen is not None:
        seen["config"] = path
    return config, str(path)


def test_serve_demo_prepares_and_serves(tmp_path, served):
    config, path = _project(tmp_path, served)
    serve.main(["serve", "--demo", "--dir", str(tmp_path / "demo")])
    assert served["demo"] == [(str(tmp_path / "demo"), None)]  # the demo picks its model as it always has
    assert served["path"] == path
    adjusted = served["adjust"](config)
    assert adjusted.mode == "demo" and adjusted.api.request_llm and adjusted.api.auth == "none"
    assert served["hosted"] is None and served["run"]["host"] == "127.0.0.1"


@pytest.mark.parametrize("args, env, file, expected", [
    ([], None, "your_data", "your_data"),
    ([], None, "demo", "demo"),
    ([], "demo", "your_data", "demo"),
    ([], "", "demo", "demo"),                       # an empty variable is unset
    (["--demo"], "your_data", "your_data", "demo"),  # the flag beats the variable
    ([], "your_data", "demo", "your_data"),         # the variable beats the file
])
def test_serve_mode_precedence_flag_env_file(tmp_path, served, monkeypatch, args, env, file, expected):
    config, path = _project(tmp_path, served, mode=file)
    if env is not None:
        monkeypatch.setenv("NL2SQL_MODE", env)
    serve.main(["serve", *(["--config", path] if "--demo" not in args else []), *args])
    assert served["adjust"](config).mode == expected


def test_a_flag_is_beaten_by_nothing_and_hosted_wins_over_demo(tmp_path, served):
    config, _ = _project(tmp_path, served)
    serve.main(["serve", "--demo", "--hosted"])
    assert served["adjust"](config).mode == "hosted"


def test_serve_rejects_an_unknown_mode(tmp_path, served, monkeypatch):
    _, path = _project(tmp_path, served)
    monkeypatch.setenv("NL2SQL_MODE", "production")
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--config", path])
    assert "production" in str(stop.value) and "run" not in served


def test_demo_refuses_auth_none_off_loopback(tmp_path, served):
    _project(tmp_path, served)
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--demo", "--host", "0.0.0.0"])
    assert "loopback" in str(stop.value) and "run" not in served


def test_hosted_refuses_an_environment_key(tmp_path, served, monkeypatch):
    _project(tmp_path, served)
    monkeypatch.setattr(serve, "provider_keys_in_environment", lambda: {"openai": "sk-not-printed"})
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--demo", "--hosted"])
    message = str(stop.value)
    assert "openai" in message.lower() and "sk-not-printed" not in message and "run" not in served


def test_hosted_serves_visitors_the_default_role_on_replay_and_never_ollama(tmp_path, served):
    config, _ = _project(tmp_path, served)
    serve.main(["serve", "--demo", "--hosted", "--host", "0.0.0.0", "--questions-per-minute", "3",
                "--questions-per-session", "9"])
    assert served["demo"][0][1] == "replay"
    hosted = served["hosted"]
    assert (hosted.limits.per_minute, hosted.limits.per_session, hosted.role) == (3, 9, "viewer")
    adjusted = served["adjust"](config)
    assert adjusted.mode == "hosted" and adjusted.api.request_llm and adjusted.api.auth == "static"
    assert adjusted.api.static_role == "viewer" and adjusted.policies.default_role == "viewer"
    assert served["env_files"] == []  # the hosted server loads no .env: it holds no key


def test_a_hosted_visitor_role_is_never_an_admin_role(tmp_path, served):
    _project(tmp_path, served, policies={"roles": {"admin": {"description": "role", "allow": ["*"]}}, "default_role": "admin"})
    with pytest.raises(SystemExit) as stop:
        serve.main(["serve", "--demo", "--hosted"])
    assert "admin" in str(stop.value) and "run" not in served


def test_the_demo_reads_the_env_file_beside_its_project(tmp_path, served):
    _project(tmp_path, served)
    serve.main(["serve", "--demo", "--dir", str(tmp_path / "demo")])
    assert served["env_files"] == [str(tmp_path / "demo" / "nl2sql.yaml")]


# ---- the .env: loaded here, before the key check, and never by a hosted server

@pytest.mark.parametrize("via", ["flag", "variable", "file"])
def test_a_hosted_server_loads_no_env_file(tmp_path, served, monkeypatch, via):
    _, path = _project(tmp_path, served, **({"mode": "hosted"} if via == "file" else {}))
    if via == "variable":
        monkeypatch.setenv("NL2SQL_MODE", "hosted")
    serve.main(["serve", "--config", path, *(["--hosted"] if via == "flag" else [])])
    assert served["hosted"] is not None and served["env_files"] == []


def test_a_server_that_is_not_hosted_loads_the_env_file_beside_its_config(tmp_path, served):
    _, path = _project(tmp_path, served)
    serve.main(["serve", "--config", path])
    assert served["env_files"] == [path] and served["hosted"] is None


def test_a_key_in_the_env_file_beside_a_hosted_config_never_reaches_the_process(tmp_path, launched, monkeypatch):
    import os

    name = "OPENAI_" + "API_KEY"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NL2SQL_MODE", raising=False)
    make_config(tmp_path, mode="hosted", policies={"roles": {"viewer": {"description": "role", "allow": ["*"]}}, "default_role": "viewer"})
    (tmp_path / ".env").write_text(f"{name}=fake-{'k' * 12}\n", encoding="utf-8")
    serve.main(["serve", "--config", str(tmp_path / "nl2sql.yaml")])
    assert len(launched) == 1 and name not in os.environ


def test_a_key_in_the_env_file_of_a_server_that_is_not_hosted_is_loaded(tmp_path, launched, monkeypatch):
    import os

    name = "NL2SQL_SERVE_TEST_" + "VALUE"
    monkeypatch.delenv(name, raising=False)
    config = _config(tmp_path)
    (tmp_path / ".env").write_text(f"{name}=from-file\n", encoding="utf-8")
    try:
        serve.main(["serve", "--config", config])
        assert os.environ[name] == "from-file"
    finally:
        os.environ.pop(name, None)


def test_hosted_makes_its_session_key_under_the_storage_path_and_demo_alone_does_not(tmp_path, served):
    config, path = _project(tmp_path, served)
    key = Path(config.storage.path) / "hosted_session.key"

    serve.main(["serve", "--demo", "--dir", str(tmp_path / "demo")])
    assert not key.exists()
    serve.main(["serve", "--demo", "--hosted", "--dir", str(tmp_path / "demo")])
    assert key.exists() and len(key.read_bytes()) == 32


def test_hosted_ends_on_replay_even_when_the_project_file_names_a_keyed_provider(tmp_path, served):
    """The Space's image is built with a project already on disk; prepare_demo keeps a file it finds."""
    from nl2sql import LLM

    config, _ = _project(tmp_path, served)
    config = config.model_copy(update={"llm": LLM(provider="openai", model="gpt-x")})
    serve.main(["serve", "--demo", "--hosted"])
    adjusted = served["adjust"](config)
    assert (adjusted.llm.provider, adjusted.llm.agents) == ("replay", {})
    unhosted = _project(tmp_path, served)[0].model_copy(update={"llm": LLM(provider="openai", model="gpt-x")})
    serve.main(["serve", "--demo"])
    assert served["adjust"](unhosted).llm.provider == "openai"
