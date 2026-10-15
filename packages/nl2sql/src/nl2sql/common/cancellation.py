from __future__ import annotations

import threading
from typing import Any, Optional


class CancellationToken:
    """Per-run cancellation flag, with why the run was cancelled.

    Each run owns its own token, so cancelling one run never affects another.
    Runs pass their token to the graph via
    ``config={"configurable": {"cancellation_token": token}}``.

    ``reason`` is None for a caller's cancel or a timeout; a ``Budget`` that
    stops the run at a limit cancels it with a ``RunStop``, which the run maps
    to ``status: "stopped"``. The first reason is kept: cancelling again
    (the timeout after a limit) does not replace it.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self.reason: Optional[Any] = None

    def cancel(self, reason: Optional[Any] = None) -> None:
        with self._lock:
            if not self._event.is_set():
                self.reason = reason
            self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout=timeout)
