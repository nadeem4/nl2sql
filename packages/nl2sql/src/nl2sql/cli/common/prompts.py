"""Interactive prompts that survive a terminal which cannot host them.

Git Bash / MSYS on Windows present a TTY that is not a Windows console, so
``prompt_toolkit`` -- which InquirerPy builds every prompt on -- refuses to
attach and raises ``NoConsoleScreenBufferError``. That killed
``nl2sql setup --demo --docker`` with a traceback *after* it had already
written the demo artifacts, which is the exact flow the docs tell users to run.

The convention here: a prompt that cannot be shown is answered the way a
non-interactive run would answer it, and the caller states that answer
explicitly rather than inheriting the interactive default. The two are not the
same -- a prompt guarding an action with side effects defaults to "yes" when a
human is watching and to "no" when nobody can say otherwise.
"""

from __future__ import annotations

import sys

from InquirerPy import inquirer
from rich.markup import escape

from nl2sql.cli.console import console

if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
else:
    class NoConsoleScreenBufferError(Exception):
        """Stand-in for the Windows-only prompt_toolkit error.

        ``prompt_toolkit.output.win32`` asserts ``sys.platform == "win32"`` at
        import time, so the real class cannot be imported elsewhere. This one is
        never raised in production; it only keeps the ``except`` clause below
        importable (and testable) on every platform.
        """


def confirm(message: str, default: bool, when_unavailable: bool) -> bool:
    """Ask a yes/no question, falling back when no prompt can be displayed.

    ``default`` is the pre-selected answer for a human; ``when_unavailable`` is
    the answer used when the terminal cannot host a prompt at all.
    """
    try:
        return inquirer.confirm(message=message, default=default).execute()
    except (NoConsoleScreenBufferError, EOFError):
        answer = "yes" if when_unavailable else "no"
        console.print(
            f"[yellow]No usable interactive console (Git Bash/MSYS or a "
            f"redirected terminal). Assuming '{answer}' for:[/yellow] "
            f"{escape(message)}"
        )
        return when_unavailable
