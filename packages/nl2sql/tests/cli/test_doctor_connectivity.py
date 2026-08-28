"""``nl2sql doctor`` must diagnose, never crash.

``verify_connectivity`` used to import ``load_profiles`` from
``nl2sql.datasources`` and ``check_connectivity`` from ``nl2sql.diagnostics``.
Neither exists, and both imports sat *outside* the function's ``try``, so the
one command a user runs when their setup is already broken died with an
``ImportError`` traceback.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nl2sql.cli import checks
from nl2sql.cli.main import app

runner = CliRunner()

# A closing-tag-shaped sequence with no matching opening tag. Rich raises
# MarkupError on it, so a driver error carrying one must not reach markup
# parsing. See test_cli_error_markup.py.
MARKUP_MARKER = "[/{style}]"


def _write_config(root, body: str):
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "datasources.yaml").write_text(body, encoding="utf-8")


def _sqlite_config(root, *ids: str) -> None:
    entries = "\n".join(
        f"- id: {ds_id}\n"
        f"  connection:\n"
        f"    type: sqlite\n"
        f"    database: data/{ds_id}.db\n"
        for ds_id in ids
    )
    _write_config(root, f"version: 1\ndatasources:\n{entries}")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Run inside a throwaway project so only the config under test is visible."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATASOURCE_CONFIG", raising=False)
    monkeypatch.delenv("SECRETS_CONFIG", raising=False)
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def captured_console(monkeypatch):
    """Point the checks module's console at a buffer and hand back the buffer."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        checks, "console", Console(file=buffer, width=200, force_terminal=False)
    )
    return buffer


class _StubAdapter:
    def __init__(self, result):
        self._result = result

    def test_connection(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _StubRegistry:
    """Stands in for DatasourceRegistry, keyed by datasource id."""

    def __init__(self, outcomes):
        self._outcomes = outcomes

    def __call__(self, secret_manager):
        return self

    def register_datasource(self, config):
        return _StubAdapter(self._outcomes[config.id])


def test_returns_true_when_every_adapter_connects(project, captured_console):
    _sqlite_config(project, "alpha", "beta")

    assert checks.verify_connectivity(print_table=True) is True

    output = captured_console.getvalue()
    assert "alpha" in output
    assert "beta" in output
    assert "OK" in output


def test_returns_false_when_one_datasource_fails(project, captured_console):
    _sqlite_config(project, "alpha")
    _write_config(
        project,
        "version: 1\n"
        "datasources:\n"
        "- id: alpha\n"
        "  connection:\n"
        "    type: sqlite\n"
        "    database: data/alpha.db\n"
        "- id: broken\n"
        "  connection:\n"
        "    type: not_a_real_engine\n",
    )

    assert checks.verify_connectivity(print_table=True) is False

    output = captured_console.getvalue()
    # The healthy datasource is still reported: one bad entry must not abort
    # the whole check.
    assert "alpha" in output
    assert "broken" in output
    assert "Failed" in output


def test_adapter_raising_inside_test_connection_is_reported(
    project, captured_console, monkeypatch
):
    _sqlite_config(project, "alpha")
    monkeypatch.setattr(
        checks,
        "DatasourceRegistry",
        _StubRegistry({"alpha": RuntimeError(f"driver exploded {MARKUP_MARKER}")}),
    )

    assert checks.verify_connectivity(print_table=True) is False

    output = captured_console.getvalue()
    assert "driver exploded" in output
    assert MARKUP_MARKER in output


def test_adapter_returning_false_is_reported(project, captured_console, monkeypatch):
    _sqlite_config(project, "alpha")
    monkeypatch.setattr(checks, "DatasourceRegistry", _StubRegistry({"alpha": False}))

    assert checks.verify_connectivity(print_table=True) is False
    assert "Failed" in captured_console.getvalue()


def test_missing_config_is_reported_not_raised(project, captured_console):
    assert checks.verify_connectivity(print_table=True) is False
    assert "configs" in captured_console.getvalue()


def test_unreadable_config_is_reported_not_raised(project, captured_console):
    _write_config(project, "datasources: [ this: is: not: yaml")

    assert checks.verify_connectivity(print_table=True) is False
    assert captured_console.getvalue().strip() != ""


def test_zero_datasources_is_reported(project, captured_console):
    _write_config(project, "version: 1\ndatasources: []")

    assert checks.verify_connectivity(print_table=True) is True
    assert "No datasources" in captured_console.getvalue()


def test_unresolvable_secret_is_reported_not_raised(project, captured_console):
    _write_config(
        project,
        "version: 1\n"
        "datasources:\n"
        "- id: alpha\n"
        "  connection:\n"
        "    type: sqlite\n"
        "    database: ${env:NO_SUCH_VARIABLE_FOR_DOCTOR}\n",
    )

    assert checks.verify_connectivity(print_table=True) is False
    assert "alpha" in captured_console.getvalue()


def test_doctor_command_exits_zero_and_prints_the_table(project):
    """The smoke test that would have caught the phantom imports."""
    _sqlite_config(project, "alpha")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "Connectivity" in result.output
    assert "Datasource ID" in result.output
    assert "alpha" in result.output


def test_doctor_command_exits_zero_when_a_datasource_is_broken(project):
    _write_config(
        project,
        "version: 1\ndatasources:\n- id: broken\n  connection:\n    type: not_a_real_engine\n",
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "broken" in result.output
    assert "Failed" in result.output
