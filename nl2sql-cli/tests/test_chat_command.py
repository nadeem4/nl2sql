"""``nl2sql chat``: how the command builds the engine and the caller before the app starts."""
from __future__ import annotations

import nl2sql
from typer.testing import CliRunner

from nl2sql.testing.config import make_config
from nl2sql_cli.chat.app import ChatApp
from nl2sql_cli.main import app


def _capture(monkeypatch):
    started = {}

    def fake_run(self, *args, **kwargs):
        started["engine"], started["user"], started["datasource_id"] = self.engine, self.user, self.datasource_id

    monkeypatch.setattr(ChatApp, "run", fake_run)
    return started


def test_the_caller_is_the_named_user_and_role(tmp_path, monkeypatch):
    make_config(tmp_path)
    started = _capture(monkeypatch)

    result = CliRunner().invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "chat", "--as", "analyst",
                                      "--user", "ana", "--datasource", "chinook"])

    assert result.exit_code == 0, result.output
    user = started["user"]
    assert (user.user_id, user.roles, user.tenant_id) == ("ana", ["analyst"], "default_tenant")
    assert started["datasource_id"] == "chinook"


def test_without_flags_the_caller_is_the_os_user_with_no_role(tmp_path, monkeypatch):
    make_config(tmp_path)
    started = _capture(monkeypatch)
    monkeypatch.setattr("getpass.getuser", lambda: "sam")

    result = CliRunner().invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "chat"])

    assert result.exit_code == 0, result.output
    assert (started["user"].user_id, started["user"].roles) == ("sam", [])


def test_demo_mode_answers_from_recordings(tmp_path, monkeypatch):
    """--demo calls ``nl2sql.prepare_demo(dir)``, the function ``serve --demo`` uses, and chats on the project it returns."""
    config = make_config(tmp_path / "demo")
    seen = {}

    def prepare_demo(folder, **kwargs):
        seen["folder"] = folder
        return tmp_path / "demo" / "nl2sql.yaml"

    monkeypatch.setattr(nl2sql, "prepare_demo", prepare_demo)
    started = _capture(monkeypatch)

    result = CliRunner().invoke(app, ["chat", "--demo", "--dir", str(tmp_path / "demo")])

    assert result.exit_code == 0, result.output
    assert str(seen["folder"]) == str(tmp_path / "demo")
    assert started["engine"].config.storage.path == config.storage.path


def test_demo_mode_loads_the_demo_folders_env_and_not_its_parents(tmp_path, monkeypatch):
    import os

    make_config(tmp_path / "demo")
    (tmp_path / "demo" / ".env").write_text("T1_PROBE_DEMO=yes\n", encoding="utf-8")
    (tmp_path / ".env").write_text("T1_PROBE_PARENT=yes\n", encoding="utf-8")
    for name in ("T1_PROBE_DEMO", "T1_PROBE_PARENT"):
        monkeypatch.setenv(name, "")  # so the teardown restores the variable
        monkeypatch.delenv(name)
    monkeypatch.setattr(nl2sql, "prepare_demo", lambda folder, **kwargs: tmp_path / "demo" / "nl2sql.yaml")
    _capture(monkeypatch)

    result = CliRunner().invoke(app, ["chat", "--demo", "--dir", str(tmp_path / "demo")])

    assert result.exit_code == 0, result.output
    assert os.environ.get("T1_PROBE_DEMO") == "yes"
    assert "T1_PROBE_PARENT" not in os.environ
