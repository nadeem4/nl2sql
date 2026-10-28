"""Finding a database: ``engine.datasources.scan()`` (docs/architecture/wave_c.md, section 7, D120).

Three places, each declared by the adapters: a URL the caller passes, database files under a folder, and the
default ports on the loopback address. The adapters name the suffixes, ports and schemes; this module names no
database. It opens no database session: a file's first bytes are read by the adapter's ``connection_for``, a port
is only knocked on, and an adapter is never built.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

from nl2sql.api.types import Candidate
from nl2sql.registry import Registry, missing_modules
from nl2sql_sdk.discovery import DiscoverableAdapter, Found

MAX_DEPTH = 2  # folders below the scanned one
MAX_FILES = 50  # files with a declared suffix that are looked at
PORT_TIMEOUT_SECONDS = 0.2
LOOPBACK = "127.0.0.1"


def scan(registry: Registry, folder: Path, *, url: Optional[str], ports: bool,
         skip: Optional[Path] = None) -> List[Candidate]:
    """The candidates found at ``url``, under ``folder`` and on loopback ports, in that order, each once.

    ``skip`` is a folder to leave out (the engine's ``storage.path``).
    """
    adapters = _discoverable(registry)
    found: List[Candidate] = []
    seen: Set[Tuple[Any, ...]] = set()

    def add(name: str, cls: Any, where: Found, shown: Optional[Found] = None) -> None:
        try:
            connection = cls.connection_for(where)
            if connection is None:
                return
            key = _identity(name, cls, connection)
            if key in seen:
                return
            required = [field for field, info in cls.connection_model.model_fields.items() if info.is_required()]
            candidate = Candidate(
                type=name, title=getattr(cls, "title", None) or name, found=shown or where, connection=connection,
                missing=[field for field in required if field not in connection],
                installed=not missing_modules("adapter", cls), install_hint=getattr(cls, "install_hint", ""))
        except Exception:  # a plugin's bug or a URL it cannot read; its error text may hold a password, so drop it
            return
        seen.add(key)
        found.append(candidate)

    if url:
        scheme = urlsplit(url).scheme.partition("+")[0].lower()
        for name, cls in adapters.items():
            if scheme in cls.url_schemes:
                add(name, cls, Found(kind="url", value=url), Found(kind="url", value=_without_password(url)))
    for path in _files(folder, skip, {s.lower() for cls in adapters.values() for s in cls.file_suffixes}):
        for name, cls in adapters.items():
            if path.suffix.lower() in cls.file_suffixes:
                add(name, cls, Found(kind="file", value=str(path)))
    if ports:
        open_ports: Dict[int, bool] = {}
        for name, cls in adapters.items():
            for port in cls.default_ports:
                if port not in open_ports:
                    open_ports[port] = _is_open(port)
                if open_ports[port]:
                    add(name, cls, Found(kind="port", value=f"{LOOPBACK}:{port}"))
    return found


def _discoverable(registry: Registry) -> Dict[str, Any]:
    """Every adapter the registry names, installed or not, that declares how to be found, by name."""
    adapters = {}
    for name in registry.names("adapter"):
        try:
            cls = registry.load("adapter", name, require_installed=False)
        except Exception:  # a plugin that fails to import or its version check
            continue
        if isinstance(cls, DiscoverableAdapter):
            adapters[name] = cls
    return adapters


def _files(folder: Path, skip: Optional[Path], suffixes: Set[str]) -> Iterator[Path]:
    """Files under ``folder`` with a declared suffix: ``MAX_DEPTH`` folders down, no hidden folder, not ``skip``."""
    skipped = skip.resolve() if skip else None
    count = 0

    def walk(directory: Path, depth: int) -> Iterator[Path]:
        nonlocal count
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if count >= MAX_FILES:
                return
            if entry.is_file() and entry.suffix.lower() in suffixes:
                count += 1
                yield entry
        for entry in entries:
            if (depth < MAX_DEPTH and entry.is_dir() and not entry.name.startswith(".")
                    and entry.resolve() != skipped):
                yield from walk(entry, depth + 1)

    yield from walk(Path(folder), 0)


def _is_open(port: int) -> bool:
    """Whether something accepts a TCP connection on ``port`` of the loopback address, and nothing else."""
    try:
        socket.create_connection((LOOPBACK, port), timeout=PORT_TIMEOUT_SECONDS).close()
    except OSError:
        return False
    return True


def _identity(name: str, cls: Any, connection: Dict[str, Any]) -> Tuple[Any, ...]:
    """What makes two findings the same database: its server and port, or its file.

    A missing port is the adapter's default, and ``localhost`` is the loopback address.
    """
    if "host" in connection:
        host = LOOPBACK if str(connection["host"]).lower() == "localhost" else connection["host"]
        port = connection.get("port")
        if port is None:
            field = cls.connection_model.model_fields.get("port")
            port = field.default if field is not None and isinstance(field.default, int) else None
        return name, host, port
    return name, str(Path(str(connection.get("database"))).resolve())


def _without_password(url: str) -> str:
    """``url`` with the password replaced by ``***`` and the query and fragment dropped.

    A password can sit in the query too (``?password=``, an ODBC string), so nothing after the path is kept.
    """
    parts = urlsplit(url)
    userinfo, at, host = parts.netloc.rpartition("@")
    user, colon, _ = userinfo.partition(":")
    netloc = f"{user}:***{at}{host}" if colon else parts.netloc
    return urlunsplit(parts._replace(netloc=netloc, query="", fragment=""))
