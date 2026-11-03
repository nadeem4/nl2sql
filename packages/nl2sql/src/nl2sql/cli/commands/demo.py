"""`nl2sql demo`: start `nl2sql-api serve --demo` and open the browser on it.

The web app and the server live in ``nl2sql-api`` (wave_d.md, D211). This command only checks that package is
installed, runs ``python -m nl2sql_api serve --demo`` as a child process, opens the page once the server answers,
and forwards Ctrl+C to the child. It imports nothing of the API.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.console import print_error

INSTALL_HINT = 'The web app comes with nl2sql-api: pip install "nl2sql-engine[demo]"'

# The first start writes the demo project and builds its index, which can take a few minutes.
START_TIMEOUT = 300


def _wait_until_up(url: str, child) -> bool:
    """True once ``url`` answers; False if the child exits or ``START_TIMEOUT`` passes first."""
    deadline = time.monotonic() + START_TIMEOUT
    while child.poll() is None and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


@handle_cli_errors
def demo_command(directory: pathlib.Path, host: str, port: int, no_browser: bool) -> None:
    if importlib.util.find_spec("nl2sql_api") is None:
        print_error(INSTALL_HINT)
        raise SystemExit(1)

    argv = [sys.executable, "-m", "nl2sql_api", "serve", "--demo", "--dir", str(directory),
            "--host", host, "--port", str(port)]
    # The child gets its own process group (Windows) or session (POSIX), so Ctrl+C reaches only this launcher,
    # which forwards it once.
    own = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    child = subprocess.Popen(argv, **own)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/"
    try:
        if not no_browser and _wait_until_up(url, child):
            webbrowser.open(url)
        code = child.wait()
    except KeyboardInterrupt:
        child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        code = child.wait()
    finally:
        if child.poll() is None:  # the launcher is leaving by any other road: never leave the server behind
            child.terminate()
            child.wait()
    raise SystemExit(code)
