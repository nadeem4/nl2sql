from __future__ import annotations

import threading
from typing import Optional


class CancellationToken:
    """Per-run cancellation flag.

    Each run owns its own token, so cancelling one run never affects another.
    Runs pass their token to the graph via
    ``config={"configurable": {"cancellation_token": token}}``.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout=timeout)
