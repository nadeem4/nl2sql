from __future__ import annotations

import io
import sys

import pytest

from nl2sql.cli.console import configure_output_encoding
from nl2sql.cli.reporting import ConsolePresenter


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
    """`nl2sql setup --demo` died here with a UnicodeEncodeError.

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
        "nl2sql.cli.main.configure_output_encoding", lambda: calls.append("configured")
    )
    monkeypatch.setattr("nl2sql.cli.main.app", lambda: calls.append("app"))

    from nl2sql.cli.main import main

    main()

    assert calls == ["configured", "app"]


def test_the_presenter_degrades_its_symbols_when_the_stream_cannot_encode_them(monkeypatch):
    """`scripts/record_demo_answers.py` calls `demo_command` without `main()`.

    With stdout redirected to a file on Windows (cp1252) and no
    ``configure_output_encoding``, the check mark in ``finish_task_line``
    raised inside ``run_indexing``, so indexing "failed" and every question
    then ran against no index. The presenter must never be what kills a command.
    """
    stream = _legacy_stdout(monkeypatch)
    presenter = ConsolePresenter()

    presenter.print_success("Indexing complete.")
    presenter.finish_task_line(presenter.start_task_line("Indexing chinook..."), "chinook indexed")
    presenter.finish_task_line(presenter.start_task_line("Indexing sales..."), "sales failed", success=False)
    stream.flush()

    written = stream.buffer.getvalue().decode("cp1252")
    assert "[OK] Indexing complete." in written
    assert "[OK] chinook indexed" in written
    assert "[FAILED] sales failed" in written


def test_the_presenter_keeps_its_symbols_on_a_utf8_stream(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stream)

    ConsolePresenter().print_success("Indexing complete.")
    stream.flush()

    assert "✓ Indexing complete." in stream.buffer.getvalue().decode("utf-8")
