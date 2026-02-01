from __future__ import annotations

import threading
from typing import Optional

_cancel_event = threading.Event()


def cancel() -> None:
    _cancel_event.set()


def reset() -> None:
    _cancel_event.clear()


def is_cancelled() -> bool:
    return _cancel_event.is_set()


def wait(timeout: Optional[float] = None) -> bool:
    return _cancel_event.wait(timeout=timeout)