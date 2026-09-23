"""Hosted mode: the playground as a public demo where each visitor brings a key.

Local mode and hosted mode are two different servers, not one server with a
looser gate:

============  =========================  =================================
              local (``nl2sql demo``)    hosted (``nl2sql demo --hosted``)
============  =========================  =================================
the key       saved to ``.env.demo``     held by the browser, sent per
              and used by the process    request, used in memory, dropped
settings      written to disk            refused; the page keeps the key
rebuild       on (loopback)              refused
feedback      on (loopback)              off
``--record``  supported                  refused before the server starts
limits        none                       a token bucket and a session cap
============  =========================  =================================

This module is the hosted half: where the key is read from, what refusals read
like, and the two limits. It holds no key of its own -- :func:`api_key` returns
one to the caller, which passes it to
:func:`nl2sql.llm.request_key.use_api_key` for the length of one question and
never anywhere else.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Dict, List, Optional

from fastapi import HTTPException, Request, Response

from nl2sql.llm.providers import KEY_SHAPE_MESSAGE, looks_like_api_key

# The header the page sends its key in. A header, not the body: a body is what
# request logs, validation errors and error reports quote.
KEY_HEADER = "X-NL2SQL-Api-Key"

# The cookie the session cap counts against. It names no visitor: it is a
# random token this process made up, kept only in this process's memory.
SESSION_COOKIE = "nl2sql_demo_session"

# Turns hosted mode on for a container, where there is no command line.
HOSTED_ENV = "NL2SQL_DEMO_HOSTED"

# How many questions one visitor may ask per minute, and per session.
QUESTIONS_PER_MINUTE = "NL2SQL_DEMO_QUESTIONS_PER_MINUTE"
QUESTIONS_PER_SESSION = "NL2SQL_DEMO_QUESTIONS_PER_SESSION"
DEFAULT_QUESTIONS_PER_MINUTE = 6
DEFAULT_QUESTIONS_PER_SESSION = 30

# A visitor who has not asked for an hour is forgotten, and the maps never grow
# past this many visitors: a public demo must not run out of memory because
# someone rotated their address.
IDLE_SECONDS = 3600.0
MAX_VISITORS = 5000

NO_KEY_MESSAGE = (
    "Add your own API key under Settings to ask a question. It stays in this browser tab; "
    "the hosted demo never stores it."
)
RATE_MESSAGE = (
    "Too many questions in a row on the public demo. Wait a moment and ask again, or run the "
    "demo locally for no limit at all."
)
SESSION_MESSAGE = (
    "This browser session has used its {cap} questions on the public demo. Run the demo locally "
    "(pip install \"nl2sql-engine[demo]\" && nl2sql demo) to keep going, against your own data."
)
REBUILD_MESSAGE = (
    "Rebuilding the index is off on the hosted demo: it writes to disk and the sample data never "
    "changes. Run the demo locally to rebuild."
)
FEEDBACK_MESSAGE = (
    "Feedback is off on the hosted demo: it would keep the question and SQL of every rated run "
    "on a shared server."
)


def truthy(value: Optional[str]) -> bool:
    """Whether an environment variable reads as on."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    """A count from the environment; anything unreadable leaves the default."""
    try:
        value = int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class Limits:
    """Two caps per visitor, in this process's memory and nowhere else.

    A token bucket holds the pace down (``per_minute`` questions, refilled
    steadily) and a counter holds the total down (``per_session`` questions).
    The bucket is keyed by address so one browser cannot lift it by dropping
    its cookie; the counter is keyed by the session cookie, which is what
    "per session" means.
    """

    def __init__(self, per_minute: int = DEFAULT_QUESTIONS_PER_MINUTE,
                 per_session: int = DEFAULT_QUESTIONS_PER_SESSION,
                 clock=time.monotonic) -> None:
        self.per_minute = per_minute
        self.per_session = per_session
        self._clock = clock
        # key -> [tokens left, when that was true]
        self._buckets: Dict[str, List[float]] = {}
        # session -> [questions asked, when the last one was]
        self._asked: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def take(self, *, address: str, session: str) -> Optional[str]:
        """Spends one question, or says why it cannot be spent."""
        now = self._clock()
        with self._lock:
            self._forget(now)
            asked = self._asked.setdefault(session, [0.0, now])
            if asked[0] >= self.per_session:
                asked[1] = now
                return SESSION_MESSAGE.format(cap=self.per_session)
            refusal = self._pace(address, now)
            if refusal:
                return refusal
            asked[0] += 1
            asked[1] = now
            return None

    def pace(self, address: str) -> Optional[str]:
        """Spends one request against the pace alone, for a route that costs no tokens."""
        now = self._clock()
        with self._lock:
            self._forget(now)
            return self._pace(address, now)

    def _pace(self, address: str, now: float) -> Optional[str]:
        bucket = self._buckets.setdefault(address, [float(self.per_minute), now])
        refilled = bucket[0] + (now - bucket[1]) * self.per_minute / 60.0
        bucket[0] = min(float(self.per_minute), refilled)
        bucket[1] = now
        if bucket[0] < 1.0:
            return RATE_MESSAGE
        bucket[0] -= 1.0
        return None

    def asked(self, session: str) -> int:
        """How many questions this session has spent, for the page's counter."""
        with self._lock:
            return int(self._asked.get(session, [0.0])[0])

    def _forget(self, now: float) -> None:
        """Drops visitors who stopped asking, so neither map grows without end."""
        for store in (self._buckets, self._asked):
            if len(store) <= MAX_VISITORS:
                stale = [k for k, entry in store.items() if now - entry[1] > IDLE_SECONDS]
            else:
                stale = sorted(store, key=lambda k: store[k][1])[: len(store) - MAX_VISITORS // 2]
            for key in stale:
                store.pop(key, None)


def _address(request: Request) -> str:
    """The address the bucket is keyed by.

    A Space serves the container behind its own proxy, so the peer address is
    the proxy's for everyone; ``X-Forwarded-For``'s first hop is the visitor.
    A visitor can forge that header, and so lift their own rate limit -- the
    session cap, which is what stops a runaway, does not depend on it.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    return (request.client.host if request.client else None) or "unknown"


class Hosted:
    """Hosted mode's state: whether it is on, and the limits it enforces."""

    def __init__(self, enabled: bool = False, limits: Optional[Limits] = None) -> None:
        self.enabled = enabled
        self.limits = limits or Limits(
            per_minute=_positive_int(QUESTIONS_PER_MINUTE, DEFAULT_QUESTIONS_PER_MINUTE),
            per_session=_positive_int(QUESTIONS_PER_SESSION, DEFAULT_QUESTIONS_PER_SESSION),
        )

    def api_key(self, request: Request) -> str:
        """The key this request brought, or a refusal that says how to add one.

        Neither refusal quotes what arrived, so a mistyped key cannot end up in
        a browser console, a screenshot or a bug report.
        """
        key = (request.headers.get(KEY_HEADER) or "").strip()
        if not key:
            raise HTTPException(status_code=401, detail=NO_KEY_MESSAGE)
        if not looks_like_api_key(key):
            raise HTTPException(status_code=400, detail=KEY_SHAPE_MESSAGE)
        return key

    def session(self, request: Request, response: Response) -> str:
        """This browser's session token, minted and set on the first question."""
        existing = request.cookies.get(SESSION_COOKIE) or ""
        if existing.isalnum() and 16 <= len(existing) <= 64:
            return existing
        fresh = secrets.token_hex(16)
        response.set_cookie(SESSION_COOKIE, fresh, httponly=True, samesite="lax", max_age=86400,
                            path="/")
        return fresh

    def spend(self, request: Request, response: Response) -> None:
        """Charges one question to this visitor, or refuses with 429 and a sentence."""
        refusal = self.limits.take(address=_address(request),
                                   session=self.session(request, response))
        if refusal:
            raise HTTPException(status_code=429, detail=refusal)

    def throttle(self, request: Request) -> None:
        """Holds down a route that costs no tokens (the retrieval inspector).

        It spends a token from the same bucket, so a script cannot sit on the
        local embedder, but it does not count against the session's questions:
        those are for the ones that call a model.
        """
        refusal = self.limits.pace(_address(request))
        if refusal:
            raise HTTPException(status_code=429, detail=refusal)

    def describe(self) -> Dict[str, object]:
        """What ``/api/meta`` tells the page about hosted mode."""
        return {"hosted": self.enabled,
                "limits": {"questions_per_minute": self.limits.per_minute,
                           "questions_per_session": self.limits.per_session} if self.enabled else None}


def from_env(flag: bool = False) -> Hosted:
    """Hosted mode as the flag and the environment ask for it."""
    return Hosted(enabled=bool(flag) or truthy(os.environ.get(HOSTED_ENV)))
