"""`nl2sql demo` is a launcher: it starts `nl2sql_api serve --demo` and gets out of the way."""
import signal
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from nl2sql_cli.main import app

runner = CliRunner()


class _Child:
    """Stands in for the server process."""

    def __init__(self, argv, flags, interrupts=0, code=0):
        self.argv, self.flags, self.code = argv, flags, code
        self.interrupts = interrupts
        self.signals, self.finished, self.terminated, self.kwargs = [], False, False, {}

    def poll(self):
        return 0 if self.finished else None

    def wait(self):
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        self.finished = True
        return self.code

    def terminate(self):
        self.terminated = True
        self.finished = True

    def send_signal(self, number):
        self.signals.append(number)


class _Started(list):
    interrupts = 0
    code = 0


@pytest.fixture
def started(monkeypatch):
    """Every child the launcher starts; the page counts as up at once."""
    children = _Started()

    def popen(argv, creationflags=0, **kwargs):
        child = _Child(argv, creationflags, interrupts=children.interrupts, code=children.code)
        child.kwargs = kwargs
        children.append(child)
        return child

    monkeypatch.setattr("nl2sql_cli.commands.demo.subprocess.Popen", popen)
    monkeypatch.setattr("nl2sql_cli.commands.demo._wait_until_up", lambda url, child: True)
    monkeypatch.setattr("nl2sql_cli.commands.demo.importlib.util.find_spec", lambda name: object())
    return children


def test_demo_is_a_launcher(started, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    result = runner.invoke(app, ["demo", "--dir", str(tmp_path / "d"), "--host", "127.0.0.1", "--port", "9001"])

    assert result.exit_code == 0, result.output
    assert [c.argv for c in started] == [[
        sys.executable, "-m", "nl2sql_api", "serve", "--demo", "--dir", str(tmp_path / "d"),
        "--host", "127.0.0.1", "--port", "9001"]]
    assert opened == ["http://127.0.0.1:9001/"]


def test_the_launcher_does_not_open_a_browser_when_told_not_to(started, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    assert runner.invoke(app, ["demo", "--no-browser"]).exit_code == 0

    assert opened == []


def test_the_launcher_exits_with_the_childs_code(started):
    started.code = 3

    assert runner.invoke(app, ["demo", "--no-browser"]).exit_code == 3


def test_the_launcher_has_no_hosted_record_or_key_flags():
    for flag in ("--hosted", "--record", "--api-key", "--allow-settings", "--questions-per-minute"):
        assert runner.invoke(app, ["demo", flag]).exit_code == 2, flag


def test_launcher_forwards_ctrl_c(started):
    started.interrupts = 1

    result = runner.invoke(app, ["demo", "--no-browser"])

    assert result.exit_code == 0, result.output
    expected = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
    assert started[0].signals == [expected]
    if sys.platform == "win32":
        assert started[0].flags == subprocess.CREATE_NEW_PROCESS_GROUP


def test_without_nl2sql_api_it_prints_the_install_line(monkeypatch):
    monkeypatch.setattr("nl2sql_cli.commands.demo.importlib.util.find_spec", lambda name: None)
    started = []
    monkeypatch.setattr("nl2sql_cli.commands.demo.subprocess.Popen", lambda *a, **k: started.append(a))

    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 1
    assert 'pip install "nl2sql-cli[web]"' in " ".join(result.output.split())
    assert started == []


def test_on_posix_the_child_gets_its_own_session_and_one_sigint(started, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("nl2sql_cli.commands.demo.os", SimpleNamespace(name="posix"))
    started.interrupts = 1

    assert runner.invoke(app, ["demo", "--no-browser"]).exit_code == 0

    assert started[0].kwargs == {"start_new_session": True}
    assert started[0].signals == [signal.SIGINT]


def test_the_child_is_terminated_when_the_launcher_is_left_early(started, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: (_ for _ in ()).throw(RuntimeError("no browser")))

    runner.invoke(app, ["demo"])

    assert started[0].terminated
