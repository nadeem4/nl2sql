"""Finding a database: what an adapter declares so ``engine.datasources.scan()`` can offer it.

``DiscoverableAdapter`` is optional. An adapter without it is never found, only
chosen by name; with it, the engine matches a connection URL's scheme, files by
suffix and local ports against what each adapter declares, so the engine names
no database itself and a plugin adapter is found the same way as a built-in one
(docs/architecture/wave_c.md, section 7, D120). The adapter interface stays at
version 2: the engine tests for these with ``isinstance``.

This module imports only pydantic and the standard library.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal, Optional, Protocol, Tuple, runtime_checkable

from pydantic import BaseModel


class Found(BaseModel):
    """Where a database was found: a ``url`` the caller passed, a ``file`` under the scanned folder, or an
    open ``port`` on the loopback address."""

    kind: Literal["url", "file", "port"]
    value: str  # the URL, the file path, or "127.0.0.1:5432"


@runtime_checkable
class DiscoverableAdapter(Protocol):
    """What an adapter declares to be found. Optional."""

    file_suffixes: ClassVar[Tuple[str, ...]]  # (".sqlite", ".sqlite3", ".db")
    default_ports: ClassVar[Tuple[int, ...]]  # (5432,)
    url_schemes: ClassVar[Tuple[str, ...]]  # ("postgresql", "postgres")

    @classmethod
    def connection_for(cls, found: Found) -> Optional[Dict[str, Any]]:
        """The connection fields this finding fills in, or None when it is not this adapter's (a ``.db``
        file that is not SQLite). Never connects."""
        ...


__all__ = ["Found", "DiscoverableAdapter"]
