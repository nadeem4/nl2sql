"""Importing the library must not touch the host application's logging setup."""
import subprocess
import sys
import textwrap


CHILD_SCRIPT = textwrap.dedent(
    """
    import logging

    root = logging.getLogger()
    before = [id(h) for h in root.handlers]
    before_level = root.level

    import nl2sql  # noqa: F401

    after = [id(h) for h in root.handlers]
    print("BEFORE=%r AFTER=%r LEVELS=%r/%r" % (before, after, before_level, root.level))
    print("UNCHANGED" if before == after else "CHANGED")
    """
)


def test_importing_nl2sql_does_not_reconfigure_root_logger():
    """A library must never add/remove root handlers just because it was imported.

    Run in a subprocess: pytest installs its own root handlers, so the parent
    process cannot observe the import-time side effect honestly.
    """
    result = subprocess.run(
        [sys.executable, "-c", CHILD_SCRIPT],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "UNCHANGED" in result.stdout, (
        "importing nl2sql mutated the root logger's handlers: " + result.stdout
    )
