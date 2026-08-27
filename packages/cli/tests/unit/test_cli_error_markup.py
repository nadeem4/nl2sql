"""The CLI error handler must show the real error, not a rich MarkupError.

``Console.print`` parses its argument as rich markup, so any exception text or
traceback containing a bracket sequence that looks like a closing tag (rich's
own source carries ``[/{style}]``) used to raise ``MarkupError`` *instead of*
displaying the error the user actually hit.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from nl2sql.common.exceptions import NL2SQLError
from nl2sql_cli.common import decorators

# A closing-tag-shaped sequence with no matching opening tag: rich raises
# MarkupError on it. It is lifted from rich's own source, which is how a real
# traceback picked it up.
MARKER = "[/{style}]"


@pytest.fixture
def captured_console(monkeypatch):
    """Point the decorator's console at a buffer and hand back the buffer."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        decorators, "console", Console(file=buffer, width=200, force_terminal=False)
    )
    return buffer


def _inner_boom():
    raise RuntimeError("plain message with no brackets")


def _raise_with_marked_traceback():
    _inner_boom()  # traceback marker: [/{style}]


def test_nl2sql_error_message_with_markup_is_displayed(captured_console):
    @decorators.handle_cli_errors
    def command():
        raise NL2SQLError(f"connection failed {MARKER} at host")

    with pytest.raises(SystemExit) as exc_info:
        command()

    assert exc_info.value.code == 1
    output = captured_console.getvalue()
    assert MARKER in output
    assert "connection failed" in output
    assert "at host" in output


def test_unexpected_error_message_with_markup_is_displayed(captured_console):
    @decorators.handle_cli_errors
    def command():
        raise ValueError(f"bad value {MARKER} rejected")

    with pytest.raises(SystemExit) as exc_info:
        command()

    assert exc_info.value.code == 1
    output = captured_console.getvalue()
    assert MARKER in output
    assert "bad value" in output
    assert "rejected" in output


def test_traceback_containing_markup_is_displayed(captured_console):
    @decorators.handle_cli_errors
    def command():
        _raise_with_marked_traceback()

    with pytest.raises(SystemExit) as exc_info:
        command()

    assert exc_info.value.code == 1
    output = captured_console.getvalue()
    # The offending frame is rendered verbatim, brackets and all.
    assert MARKER in output
    assert "plain message with no brackets" in output
    assert "RuntimeError" in output


# ---------------------------------------------------------------------------
# Presenter and shared console helpers
# ---------------------------------------------------------------------------


def _render(func, *args, **kwargs) -> str:
    """Run a presenter call against a buffered console and return the output."""
    from nl2sql_cli.reporting import ConsolePresenter

    buffer = io.StringIO()
    presenter = ConsolePresenter(
        console=Console(file=buffer, width=200, force_terminal=False)
    )
    getattr(presenter, func)(*args, **kwargs)
    return buffer.getvalue()


def test_presenter_error_path_displays_markup_text():
    # print_error/warning/info/success all funnel through _print_labeled.
    assert MARKER in _render("print_error", f"pipeline blew up {MARKER}")
    assert MARKER in _render("print_warning", f"careful {MARKER}")
    assert MARKER in _render("print_info", f"note {MARKER}")


def test_presenter_renders_bracket_quoted_sql_verbatim():
    # T-SQL bracket-quoted identifiers were silently swallowed as style tags.
    output = _render("print_sql", "SELECT * FROM [dbo].[orders]")
    assert "[dbo].[orders]" in output


def test_presenter_result_rows_with_brackets_are_displayed():
    rows = [{"note": f"value {MARKER} here"}]
    output = _render("print_execution_result", {"rows": rows, "columns": ["note"]})
    assert MARKER in output


def test_presenter_execution_tree_escapes_query_and_sql():
    output = _render(
        "print_execution_tree",
        f"how many rows match {MARKER}?",
        [{"sub_query": f"count {MARKER}", "datasource_id": "demo", "sql": "SELECT * FROM [dbo].[t]"}],
    )
    assert MARKER in output
    assert "[dbo].[t]" in output


def test_shared_console_helpers_escape_message(monkeypatch):
    from nl2sql_cli import console as console_module

    buffer = io.StringIO()
    monkeypatch.setattr(
        console_module, "console", Console(file=buffer, width=200, force_terminal=False)
    )
    console_module.print_error(f"install failed {MARKER}")
    console_module.print_success(f"wrote nl2sql-postgres[all] {MARKER}")
    output = buffer.getvalue()
    assert output.count(MARKER) == 2
    assert "nl2sql-postgres[all]" in output
