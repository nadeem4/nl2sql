"""The hosted demo's state and the middleware that enforces it (docs/architecture/wave_d.md, D221 and "The hosted demo").

``nl2sql-api serve --demo --hosted`` is a public server where nobody is trusted and nothing is kept for long:

* **Identity.** Every browser is an anonymous session: a random token in the ``HttpOnly`` cookie
  :data:`SESSION_COOKIE` (a 128-bit nonce and its HMAC under a key kept in the storage directory, so nothing is stored
  per visitor, a token the server did not sign is replaced, and a restart keeps every cookie valid). The token is a
  secret, so it only finds the session: the middleware turns it into a one-way
  visitor id (:meth:`Hosted.visitor_id`) and everything after that (the ``user_id`` with one non-admin role, the
  limits, the job and stream caps, every owner the engine stores) uses the id, never the token. It is not a value of
  ``api.auth``:
  :func:`nl2sql_api.identity.caller` asks :meth:`Hosted.user` first, so no header makes a visitor an admin and the
  admin routes answer 403 for the same reason they do anywhere else.
* **Limits.** Questions, turns and ``POST /jobs/<kind>`` spend from a token bucket (per client address: the first hop
  of ``X-Forwarded-For``, which the client writes) and a per-session count (per cookie, which the client keeps or
  drops). They hold an honest browser to its share; they do not stop a script, which sends no cookie and a made-up
  address. A session has at most one job and two open streams. A refusal is a 429
  ``LIMIT_REACHED`` with ``Retry-After``, sent before the route runs, so it never starts a model call.

The signing key is the one thing on disk (``hosted_session.key`` in the storage directory); the rest of this state is in
this process's memory only, and a visitor who has been idle for an hour is forgotten.
"""
import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nl2sql import UserContext
from nl2sql_api.errors import ApiError, ErrorBody

# The cookie that finds a visitor. It names no one: a random token this process made up and remembers (Hosted.mint).
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
    (``per_session`` questions). The bucket is keyed by address; the counter by the visitor id (:meth:`Hosted.visitor_id`),
    which is what "per session" means.
    """

    def __init__(self, per_minute: int = DEFAULT_QUESTIONS_PER_MINUTE, per_session: int = DEFAULT_QUESTIONS_PER_SESSION,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.per_session, self._clock = per_minute, per_session, clock
        self._buckets: Dict[str, List[float]] = {}  # address -> [tokens left, when that was true]
        self._asked: Dict[str, List[float]] = {}  # visitor -> [questions asked, when the last one was]
        self._lock = threading.Lock()

    def take(self, *, address: str, visitor: str) -> Optional[Refusal]:
        """Spends one question, or says why it cannot be spent."""
        now = self._clock()
        with self._lock:
            self._forget(now)
            asked = self._asked.setdefault(visitor, [0.0, now])
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

    def asked(self, visitor: str) -> int:
        """How many questions this visitor has spent, for the page's counter."""
        with self._lock:
            return int(self._asked.get(visitor, [0.0])[0])

    def _forget(self, now: float) -> None:
        for store in (self._buckets, self._asked):
            if len(store) <= MAX_VISITORS:
                stale = [k for k, entry in store.items() if now - entry[1] > IDLE_SECONDS]
            else:
                stale = sorted(store, key=lambda k: store[k][1])[: len(store) - MAX_VISITORS // 2]
            for key in stale:
                store.pop(key, None)


class Hosted:
    """Hosted mode's state: the role every visitor holds, the limits, and the streams each visitor has open."""

    def __init__(self, role: Optional[str], limits: Optional[Limits] = None, key: Optional[bytes] = None) -> None:
        self.role = role
        self._key = key if key is not None else secrets.token_bytes(32)  # without one, tokens last only as long as this process
        self.limits = limits or Limits()
        self._streams: Dict[str, int] = {}
        self._starting: set = set()  # visitors whose job start is being handled: the slot a parallel start cannot take
        self._lock = threading.Lock()

    @staticmethod
    def visitor_id(token: str) -> str:
        """The id of the visitor whose cookie is ``token``: a SHA-256 of it, cut to 128 bits, safe to store, show and
        log. The token is 128 random bits, so the id cannot be run back to it and knowing the id lets nobody act as
        the visitor. A plain hash is enough and needs no new configuration: an HMAC key would add nothing against
        an input that is already unguessable."""
        return "visitor-" + hashlib.sha256(b"nl2sql-hosted-visitor:" + token.encode()).hexdigest()[:32]

    def mint(self) -> str:
        """A new session token: a random 128-bit nonce and its HMAC-SHA256 under the server's key (cut to 128 bits),
        64 hex characters. Nothing is stored, so no number of visitors can push another out."""
        nonce = secrets.token_bytes(16)
        return nonce.hex() + self._sign(nonce).hex()

    def knows(self, token: str) -> bool:
        """Whether this server signed ``token``. A cookie of the right form that it did not sign (one a visitor chose,
        or tampered with) is no session: a visitor cannot choose the token it will be known by (session fixation)."""
        if not _MINTED.fullmatch(token):
            return False
        nonce, signature = bytes.fromhex(token[:32]), bytes.fromhex(token[32:])
        return hmac.compare_digest(signature, self._sign(nonce))

    def _sign(self, nonce: bytes) -> bytes:
        return hmac.new(self._key, nonce, hashlib.sha256).digest()[:16]

    def user(self, visitor: str, tenant_id: Optional[str] = None) -> UserContext:
        """The visitor with id ``visitor`` (from :meth:`visitor_id`): anonymous, with the one role, never an admin."""
        return UserContext(user_id=visitor, tenant_id=tenant_id, roles=[self.role] if self.role else [])

    def open_stream(self, visitor: str) -> bool:
        """Reserves one of the visitor's streams; False when it already has :data:`STREAMS_PER_SESSION` open."""
        with self._lock:
            if self._streams.get(visitor, 0) >= STREAMS_PER_SESSION:
                return False
            self._streams[visitor] = self._streams.get(visitor, 0) + 1
            return True

    def close_stream(self, visitor: str) -> None:
        with self._lock:
            left = self._streams.get(visitor, 0) - 1
            if left > 0:
                self._streams[visitor] = left
            else:
                self._streams.pop(visitor, None)

    def start_job(self, visitor: str) -> bool:
        """Reserves the visitor's one job slot, atomically; False when a start is already under way."""
        with self._lock:
            if visitor in self._starting:
                return False
            self._starting.add(visitor)
            return True

    def end_job(self, visitor: str) -> None:
        with self._lock:
            self._starting.discard(visitor)

    def job_starting(self, visitor: str) -> bool:
        with self._lock:
            return visitor in self._starting

    def describe(self, visitor: str) -> Dict[str, int]:
        """What ``GET /meta`` tells a visitor about the limits."""
        return {"questions_per_minute": self.limits.per_minute, "questions_per_session": self.limits.per_session,
                "asked": self.limits.asked(visitor), "jobs": JOBS_PER_SESSION, "streams": STREAMS_PER_SESSION}


# What spends a question: a question, a turn (or its retry or stream), builder proposals (which may ask the model) and
# the start of any job (the dashboard build included). A cancel is a DELETE.
_SPENDS = re.compile(r"^/api/v1/(query(/stream)?|conversations/[^/]+/(turns(/stream)?|retry)|dashboard-builder/proposals|jobs/[^/]+)$")
_STREAMS_POST = re.compile(r"^/api/v1/(query/stream|conversations/[^/]+/turns/stream)$")
_STREAMS_GET = re.compile(r"^/api/v1/jobs/[^/]+/events$")
_JOB_START = re.compile(r"^/api/v1/jobs/[^/]+$")
_MINTED = re.compile(r"[0-9a-f]{64}", re.ASCII)  # what Hosted.mint makes: nonce and signature


def _address(scope: Scope) -> str:
    """The address the bucket is keyed by: ``X-Forwarded-For``'s first hop (the Space's proxy puts the visitor there),
    else the peer. A visitor can write the header, so this paces an honest browser and nothing more."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first[:64]
    client = scope.get("client")
    return (client[0] if client else None) or "unknown"


def _https(scope: Scope) -> bool:
    """Whether the visitor reached us over HTTPS: the scheme, or the proxy's ``X-Forwarded-Proto``."""
    if scope.get("scheme") in ("https", "wss"):
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto":
            return value.decode("latin-1").split(",")[0].strip().lower() == "https"
    return False


KEY_FILE = "hosted_session.key"


def load_session_key(directory: Path) -> bytes:
    """The key that signs session tokens, read from ``directory`` or made there on first use (32 random bytes,
    readable by the owner only where the OS supports it). It lives beside the engine's own state, which survives a
    restart, so a restart keeps every visitor's cookie valid; a key made per process would orphan their work. Never
    read from the environment."""
    path = Path(directory) / KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{KEY_FILE}.{secrets.token_hex(4)}")
        descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as made:
            made.write(secrets.token_bytes(32))
        try:
            os.link(temp, path)  # whole or not at all, and the first of several workers to start wins
            os.unlink(temp)
        except FileExistsError:
            os.unlink(temp)
        except OSError:
            os.replace(temp, path)  # a volume without hard links: still whole, though the last worker to start wins
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError(f"{path} should hold exactly 32 bytes and holds {len(key)}. Delete it to make a new key "
                         "(every visitor then starts a new session).")
    return key


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
        fresh = not self.hosted.knows(session)  # only a token this server minted; anything else gets a new session
        if fresh:
            session = self.hosted.mint()
        visitor = Hosted.visitor_id(session)  # from here on the token is only for the Set-Cookie below
        scope.setdefault("state", {})["hosted_visitor"] = visitor

        async def send_with_session(message: Message) -> None:
            if fresh and message["type"] == "http.response.start":
                cookie = f"{SESSION_COOKIE}={session}; HttpOnly; Max-Age=86400; Path=/; SameSite=lax"
                if _https(scope):
                    cookie += "; Secure"
                message = {**message, "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())]}
            await send(message)

        method = scope["method"]
        streams = bool(_STREAMS_POST.match(path) if method == "POST" else method == "GET" and _STREAMS_GET.match(path))
        job = method == "POST" and bool(_JOB_START.match(path))
        refusal = self._refusal(scope, visitor, method, path, streams, job)
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
                self.hosted.close_stream(visitor)
            if job:
                self.hosted.end_job(visitor)

    def _refusal(self, scope: Scope, visitor: str, method: str, path: str, streams: bool,
                 job: bool) -> Optional[Refusal]:
        """Why this request is refused, else None. A stream or job slot it reserves is the caller's to give back
        when it returns None, and is given back here when it refuses."""
        app = scope.get("app")
        if job:
            jobs = getattr(getattr(app, "state", None), "jobs", None)
            engine = getattr(getattr(app, "state", None), "engine", None)
            tenant = engine.config.settings.tenant_id if engine is not None else None
            if jobs is not None and any(job.status in ("queued", "running")
                                        for job in jobs.list(self.hosted.user(visitor, tenant))):
                return Refusal(JOB_MESSAGE, 5)
            # The job is only submitted inside the route, so parallel starts would all pass the check above:
            # one reserves the slot, the others are refused until the response is done.
            if not self.hosted.start_job(visitor):
                return Refusal(JOB_MESSAGE, 5)
        if streams and not self.hosted.open_stream(visitor):
            if job:
                self.hosted.end_job(visitor)
            return Refusal(STREAM_MESSAGE.format(cap=STREAMS_PER_SESSION), 5)
        if method == "POST" and _SPENDS.match(path):
            refusal = self.hosted.limits.take(address=_address(scope), visitor=visitor)
            if refusal is not None:
                if streams:
                    self.hosted.close_stream(visitor)
                if job:
                    self.hosted.end_job(visitor)
            return refusal
        return None
