from __future__ import annotations

import io
import sys

import pytest

from nl2sql_cli.console import configure_output_encoding
from nl2sql_cli.reporting import ConsolePresenter


def _legacy_stdout(monkeypatch) -> io.TextIOWrapper:
    """Stand in for a default Windows console, whose code page is cp1252."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    return stream


def test_the_check_mark_is_unencodable_on_a_legacy_code_page(monkeypatch):
    """Guard the premise the fix rests on.

    If this ever stops raising, the rest of the module is testing nothing.
    """
    stream = _legacy_stdout(monkeypatch)

    with pytest.raises(UnicodeEncodeError):
        stream.write("✓")
        stream.flush()


def test_cli_output_survives_a_legacy_code_page(monkeypatch):
    """`nl2sql setup --demo --lite` died here with a UnicodeEncodeError.

    ``ConsolePresenter.print_success`` writes U+2713, which cp1252 cannot
    encode, so the command aborted unless the user set PYTHONIOENCODING=utf-8.
    """
    stream = _legacy_stdout(monkeypatch)

    configure_output_encoding()
    ConsolePresenter().print_success("Indexing process finished.")
    stream.flush()

    assert "✓" in stream.buffer.getvalue().decode("utf-8")


def test_configure_output_encoding_tolerates_streams_it_cannot_reconfigure(
    monkeypatch,
):
    """Under pytest, and behind some redirections, stdout is not a TextIOWrapper."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    configure_output_encoding()  # must not raise


def test_the_entry_point_configures_the_encoding(monkeypatch):
    """The fix is only useful if it runs before any command produces output."""
    calls: list[str] = []
    monkeypatch.setattr(
        "nl2sql_cli.main.configure_output_encoding", lambda: calls.append("configured")
    )
    monkeypatch.setattr("nl2sql_cli.main.app", lambda: calls.append("app"))

    from nl2sql_cli.main import main

    main()

    assert calls == ["configured", "app"]
