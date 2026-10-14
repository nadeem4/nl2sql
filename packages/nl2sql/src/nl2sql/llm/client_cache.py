"""One engine's LLM clients, shared by the agents and sets that use the same settings.

docs/architecture/configuration_v2.md, "Replacing the LLM at runtime":

* the key is an agent's resolved client settings (every field of
  ``AgentConfig`` but its name), with the resolved API key and each header
  value only as a SHA-256 digest, so the key can be logged and never holds a
  secret;
* agents whose settings are equal share one client, and swapping back to an
  earlier LLM reuses the clients built for it;
* it is an LRU of 16. A client that falls out of it is closed once no
  ``LLMSet`` holds it any more, so a run still on an old set is never cut off.

``close()`` on a client is called when it has one. The built-in wires build
``ChatOpenAI`` and ``ChatAnthropic``, which share the client library's
process-wide connection pool and own nothing to close, so for them closing
is dropping the reference; a provider registered in code whose client holds
its own connections closes them in ``close()``.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, Optional, Tuple

from nl2sql.common.logger import get_logger

from .models import AgentConfig

logger = get_logger("nl2sql.llm")

CACHE_SIZE = 16


@dataclass(frozen=True)
class ClientKey:
    """An agent's resolved client settings. The API key is in it only as a digest."""

    fields: Tuple[Tuple[str, Any], ...]
    api_key_sha256: Optional[str]

    @classmethod
    def of(cls, agent: AgentConfig, api_key: Optional[str]) -> "ClientKey":
        """The key for ``agent`` built with ``api_key`` (the resolved value, or None)."""
        fields = tuple((name, getattr(agent, name)) for name in type(agent).model_fields
                       if name not in ("name", "api_key", "headers"))
        if agent.headers:
            fields += (("headers", tuple(sorted((header, _sha256(value.get_secret_value()))
                                                for header, value in agent.headers.items()))),)
        return cls(fields=fields, api_key_sha256=_sha256(api_key) if api_key else None)

    def __str__(self) -> str:
        settings = " ".join(f"{name}={value}" for name, value in self.fields)
        return f"{settings} key=sha256:{self.api_key_sha256[:8]}" if self.api_key_sha256 else settings

    def __repr__(self) -> str:
        return f"ClientKey({self})"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def close_client(client: Any) -> None:
    """Calls the client's ``close()``, if it has one. A failure is logged, never raised."""
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as e:  # noqa: BLE001 - closing must not break a swap or a shutdown
        logger.warning(f"Closing an LLM client failed: {e}")


class LLMClientCache:
    """Clients by ``ClientKey``: an LRU of ``size``, with a count of the sets holding each one."""

    def __init__(self, size: int = CACHE_SIZE):
        self._size = size
        self._clients: "OrderedDict[ClientKey, Any]" = OrderedDict()
        # Clients that fell out of the LRU while a set still held them.
        self._evicted: Dict[ClientKey, Any] = {}
        self._holders: Dict[ClientKey, int] = {}
        self._lock = RLock()

    def acquire(self, key: ClientKey, build: Callable[[], Any]) -> Any:
        """The client for ``key``, built with ``build`` on a miss. The caller must ``release`` it."""
        with self._lock:
            if key in self._clients:
                self._clients.move_to_end(key)
            else:
                client = self._evicted.pop(key, None)
                self._clients[key] = client if client is not None else build()
            self._holders[key] = self._holders.get(key, 0) + 1
            client = self._clients[key]
            self._evict()
            return client

    def release(self, key: ClientKey) -> None:
        """One holder of ``key`` is done with it. An evicted client is closed after its last one."""
        with self._lock:
            held = self._holders.get(key, 0) - 1
            if held > 0:
                self._holders[key] = held
                return
            self._holders.pop(key, None)
            client = self._evicted.pop(key, None)
        if client is not None:
            close_client(client)

    def _evict(self) -> None:
        while len(self._clients) > self._size:
            key, client = self._clients.popitem(last=False)
            if self._holders.get(key):
                self._evicted[key] = client
            else:
                close_client(client)

    def close(self) -> None:
        """Closes every client, held or not. The engine is shutting down."""
        with self._lock:
            clients = list(self._clients.values()) + list(self._evicted.values())
            self._clients.clear()
            self._evicted.clear()
            self._holders.clear()
        for client in clients:
            close_client(client)
