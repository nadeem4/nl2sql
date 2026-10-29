from __future__ import annotations

import threading
from typing import Any, List, Optional


class RunCancelled(Exception):
    """Raised inside the graph once its run's token is cancelled."""


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
        self._children: List["CancellationToken"] = []
        self.reason: Optional[Any] = None

    def cancel(self, reason: Optional[Any] = None) -> None:
        with self._lock:
            if not self._event.is_set():
                self.reason = reason
            self._event.set()
            children, self._children = self._children, []
        for child in children:
            child.cancel(self.reason)

    def child(self) -> "CancellationToken":
        """A token cancelled when this one is, whose own cancel stays its own: a conversation's ``ask`` step
        runs on one, so the step's timeout ends the step and a user's cancel of the turn still reaches it."""
        token = CancellationToken()
        with self._lock:
            if not self._event.is_set():
                self._children.append(token)
                return token
        token.cancel(self.reason)
        return token

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout=timeout)
