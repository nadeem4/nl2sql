"""``nl2sql-api serve``: one worker, and D205's refusal to bind a non-loopback host with ``api.auth: none``."""
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
