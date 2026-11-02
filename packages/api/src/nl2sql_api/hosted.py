"""The hosted demo's state and the middleware that enforces it (docs/architecture/wave_d.md, D221 and "The hosted demo").

``nl2sql-api serve --demo --hosted`` is a public server where nobody is trusted and nothing is kept for long:

* **Identity.** Every browser is an anonymous session: a random 128-bit id in the ``HttpOnly`` cookie
  :data:`SESSION_COOKIE`, used as the ``user_id`` with one non-admin role. It is not a value of ``api.auth``:
  :func:`nl2sql_api.identity.caller` asks :meth:`Hosted.user` first, so no header makes a visitor an admin and the
  admin routes answer 403 for the same reason they do anywhere else.
* **Limits.** Questions, turns and ``POST /jobs/<kind>`` spend from a token bucket (per address, so dropping the cookie
  does not lift it) and a per-session count. A session has at most one job and two open streams. A refusal is a 429
  ``LIMIT_REACHED`` with ``Retry-After``, sent before the route runs, so it never starts a model call.

State is in this process's memory only; a visitor who has been idle for an hour is forgotten.
"""
import math
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from typing import Callable, Dict, List, NamedTuple, Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nl2sql import UserContext
from nl2sql_api.errors import ApiError, ErrorBody

# The cookie the session cap counts against. It names no visitor: a random token this process made up.
SESSION_COOKIE = "nl2sql_demo_session"

DEFAULT_QUESTIONS_PER_MINUTE = 6
DEFAULT_QUESTIONS_PER_SESSION = 30
JOBS_PER_SESSION = 1
STREAMS_PER_SESSION = 2

# A visitor who has not asked for an hour is forgotten, and the maps never grow past this many visitors: a public
# demo must not run out of memory because someone rotated their address.
IDLE_SECONDS = 3600.0
MAX_VISITORS = 5000

RATE_MESSAGE = ("Too many questions in a row on the public demo. Wait a moment and ask again, or run the demo "
                "locally for no limit at all.")
SESSION_MESSAGE = ("This browser session has used its {cap} questions on the public demo. Run the demo locally "
                   "(pip install \"nl2sql-engine[demo]\" && nl2sql demo) to keep going, against your own data.")
JOB_MESSAGE = "This session already has a job running on the public demo. Wait for it to finish, or cancel it."
STREAM_MESSAGE = "This session already has {cap} answers streaming on the public demo. Wait for one to finish."


class Refusal(NamedTuple):
    message: str
    retry_after: int  # seconds


class Limits:
    """Two caps per visitor, in this process's memory and nowhere else.

    A token bucket holds the pace down (``per_minute`` questions, refilled steadily) and a counter holds the total down
    (``per_session`` questions). The bucket is keyed by address; the counter by the session cookie, which is what "per
    session" means.
    """

    def __init__(self, per_minute: int = DEFAULT_QUESTIONS_PER_MINUTE, per_session: int = DEFAULT_QUESTIONS_PER_SESSION,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.per_session, self._clock = per_minute, per_session, clock
        self._buckets: Dict[str, List[float]] = {}  # address -> [tokens left, when that was true]
        self._asked: Dict[str, List[float]] = {}  # session -> [questions asked, when the last one was]
        self._lock = threading.Lock()

    def take(self, *, address: str, session: str) -> Optional[Refusal]:
        """Spends one question, or says why it cannot be spent."""
        now = self._clock()
        with self._lock:
            self._forget(now)
            asked = self._asked.setdefault(session, [0.0, now])
            if asked[0] >= self.per_session:
                asked[1] = now
                return Refusal(SESSION_MESSAGE.format(cap=self.per_session), int(IDLE_SECONDS))
            bucket = self._buckets.setdefault(address, [float(self.per_minute), now])
            bucket[0] = min(float(self.per_minute), bucket[0] + (now - bucket[1]) * self.per_minute / 60.0)
            bucket[1] = now
            if bucket[0] < 1.0:
                return Refusal(RATE_MESSAGE, max(1, math.ceil((1.0 - bucket[0]) * 60.0 / self.per_minute)))
            bucket[0] -= 1.0
            asked[0] += 1
            asked[1] = now
            return None

    def asked(self, session: str) -> int:
        """How many questions this session has spent, for the page's counter."""
        with self._lock:
            return int(self._asked.get(session, [0.0])[0])

    def _forget(self, now: float) -> None:
        for store in (self._buckets, self._asked):
            if len(store) <= MAX_VISITORS:
                stale = [k for k, entry in store.items() if now - entry[1] > IDLE_SECONDS]
            else:
                stale = sorted(store, key=lambda k: store[k][1])[: len(store) - MAX_VISITORS // 2]
            for key in stale:
                store.pop(key, None)


class Hosted:
    """Hosted mode's state: the role every visitor holds, the limits, and the streams each session has open."""

    def __init__(self, role: Optional[str], limits: Optional[Limits] = None) -> None:
        self.role = role
        self.limits = limits or Limits()
        self._streams: Dict[str, int] = {}
        self._lock = threading.Lock()

    def user(self, session: str, tenant_id: Optional[str] = None) -> UserContext:
        """The visitor behind ``session``: anonymous, with the one role, never an admin."""
        return UserContext(user_id=session, tenant_id=tenant_id, roles=[self.role] if self.role else [])

    def open_stream(self, session: str) -> bool:
        """Reserves one of the session's streams; False when it already has :data:`STREAMS_PER_SESSION` open."""
        with self._lock:
            if self._streams.get(session, 0) >= STREAMS_PER_SESSION:
                return False
            self._streams[session] = self._streams.get(session, 0) + 1
            return True

    def close_stream(self, session: str) -> None:
        with self._lock:
            left = self._streams.get(session, 0) - 1
            if left > 0:
                self._streams[session] = left
            else:
                self._streams.pop(session, None)

    def describe(self, session: str) -> Dict[str, int]:
        """What ``GET /meta`` tells a visitor about the limits."""
        return {"questions_per_minute": self.limits.per_minute, "questions_per_session": self.limits.per_session,
                "asked": self.limits.asked(session), "jobs": JOBS_PER_SESSION, "streams": STREAMS_PER_SESSION}


# What spends a question: a question, a turn (or its retry or stream) and the start of any job. A cancel is a DELETE.
_SPENDS = re.compile(r"^/api/v1/(query(/stream)?|conversations/[^/]+/(turns(/stream)?|retry)|jobs/[^/]+)$")
_STREAMS_POST = re.compile(r"^/api/v1/(query/stream|conversations/[^/]+/turns/stream)$")
_STREAMS_GET = re.compile(r"^/api/v1/jobs/[^/]+/events$")
_JOB_START = re.compile(r"^/api/v1/jobs/[^/]+$")


def _address(scope: Scope) -> str:
    """The address the bucket is keyed by: ``X-Forwarded-For``'s first hop (the Space's proxy puts the visitor there),
    else the peer. A visitor can forge the header and lift their own pace; the session cap does not depend on it."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first[:64]
    client = scope.get("client")
    return (client[0] if client else None) or "unknown"


def _cookie(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:
                return ""
            morsel = jar.get(SESSION_COOKIE)
            return morsel.value if morsel else ""
    return ""


class HostedMiddleware:
    """Gives every ``/api/v1`` request a session and applies the limits before the route runs."""

    def __init__(self, app: ASGIApp, *, hosted: Hosted) -> None:
        self.app, self.hosted = app, hosted

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return
        session = _cookie(scope)
        fresh = not (session.isalnum() and 16 <= len(session) <= 64)
        if fresh:
            session = secrets.token_hex(16)
        scope.setdefault("state", {})["hosted_session"] = session

        async def send_with_session(message: Message) -> None:
            if fresh and message["type"] == "http.response.start":
                cookie = f"{SESSION_COOKIE}={session}; HttpOnly; Max-Age=86400; Path=/; SameSite=lax"
                message = {**message, "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())]}
            await send(message)

        method = scope["method"]
        streams = bool(_STREAMS_POST.match(path) if method == "POST" else method == "GET" and _STREAMS_GET.match(path))
        refusal = self._refusal(scope, session, method, path, streams)
        if refusal is not None:
            body = ErrorBody(error=ApiError(code="LIMIT_REACHED", message=refusal.message,
                                            fix="Wait and try again.")).model_dump(mode="json")
            response = JSONResponse(body, status_code=429, headers={"Retry-After": str(refusal.retry_after)})
            await response(scope, receive, send_with_session)
            return
        try:
            await self.app(scope, receive, send_with_session)
        finally:
            if streams:
                self.hosted.close_stream(session)

    def _refusal(self, scope: Scope, session: str, method: str, path: str, streams: bool) -> Optional[Refusal]:
        """Why this request is refused, else None. A stream it reserves is the caller's to give back."""
        app = scope.get("app")
        if method == "POST" and _JOB_START.match(path):
            jobs = getattr(getattr(app, "state", None), "jobs", None)
            engine = getattr(getattr(app, "state", None), "engine", None)
            tenant = engine.config.settings.tenant_id if engine is not None else None
            if jobs is not None and any(job.status in ("queued", "running")
                                        for job in jobs.list(self.hosted.user(session, tenant))):
                return Refusal(JOB_MESSAGE, 5)
        if streams and not self.hosted.open_stream(session):
            return Refusal(STREAM_MESSAGE.format(cap=STREAMS_PER_SESSION), 5)
        if method == "POST" and _SPENDS.match(path):
            refusal = self.hosted.limits.take(address=_address(scope), session=session)
            if refusal is not None and streams:
                self.hosted.close_stream(session)
            return refusal
        return None
