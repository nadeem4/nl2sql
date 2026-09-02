"""`nl2sql setup --demo --docker` must not die on a terminal without a console.

Git Bash / MSYS report a TTY that prompt_toolkit cannot attach to, so the
"Start Docker containers now?" confirm raised NoConsoleScreenBufferError and
the command exited with a traceback -- after the demo artifacts were already
written, so the crash was pure noise on the flow the docs tell users to run.

The condition is simulated rather than reproduced: the real error is
Windows-only, so these tests would not run in CI otherwise.
"""

from __future__ import annotations

import pytest

from nl2sql.cli.commands import setup as wizard
from nl2sql.cli.common import prompts


class _StubDemoManager:
    """Records what the demo path did, without touching the disk or Docker."""

    def __init__(self, console, project_root):
        self.project_root = project_root
        self.started = []

    def setup_docker(self, api_key=None):
        return self.project_root / "demo_docker"

    def start_docker_containers(self, docker_dir):
        self.started.append(docker_dir)
        return True


@pytest.fixture()
def demo_manager(monkeypatch, tmp_path):
    """Install the stub and hand the test the instance the command builds."""
    made = []

    def _factory(console, project_root):
        manager = _StubDemoManager(console, tmp_path)
        made.append(manager)
        return manager

    monkeypatch.setattr(wizard, "DemoManager", _factory)
    monkeypatch.setattr(wizard, "PROJECT_ROOT", tmp_path)
    return made


def _unusable_console(*args, **kwargs):
    # On Windows this is prompt_toolkit's own class, which builds its own
    # "Found xterm-256color, while expecting a Windows console" message and
    # therefore takes no arguments.
    raise prompts.NoConsoleScreenBufferError()


def test_docker_setup_survives_an_unusable_prompt(monkeypatch, demo_manager):
    monkeypatch.setattr(prompts.inquirer, "confirm", _unusable_console)

    wizard.setup_command(demo=True, lite=False, docker=True)

    assert demo_manager, "the command never built a DemoManager"
    assert demo_manager[0].started == [], (
        "an unusable prompt was treated as consent to start containers"
    )


def test_docker_setup_still_starts_containers_when_confirmed(monkeypatch, demo_manager):
    # `setup` imports the helper by name, so the binding to replace is its own.
    monkeypatch.setattr(wizard, "confirm", lambda *a, **k: True)

    wizard.setup_command(demo=True, lite=False, docker=True)

    assert demo_manager[0].started == [demo_manager[0].project_root / "demo_docker"]


def test_confirm_returns_the_non_interactive_answer_not_the_default(monkeypatch):
    """The fallback is the caller's explicit answer, never the human default."""
    monkeypatch.setattr(prompts.inquirer, "confirm", _unusable_console)

    assert prompts.confirm("Do it?", default=True, when_unavailable=False) is False
    assert prompts.confirm("Do it?", default=False, when_unavailable=True) is True
