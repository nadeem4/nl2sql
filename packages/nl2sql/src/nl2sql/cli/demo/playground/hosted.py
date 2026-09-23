"""Hosted mode: the playground as a public demo where each visitor brings a key.

Local mode and hosted mode are two different servers, not one server with a
looser gate:

============  =========================  =================================
              local (``nl2sql demo``)    hosted (``nl2sql demo --hosted``)
============  =========================  =================================
the keys      saved to ``.env.demo``     held by the browser, one per
              and used by the process    provider, sent per request, used
                                         in memory, dropped
model/step    written to disk            sent per request; nothing is saved
settings      written to disk            saves nothing; the page keeps both
rebuild       on (loopback)              refused
feedback      on (loopback)              off
``--record``  supported                  refused before the server starts
limits        none                       a token bucket and a session cap
============  =========================  =================================

Choosing a model does not need the server to remember anything, so hosted mode
keeps it: the choice travels with the question, in
:data:`MODELS_HEADER`, and each provider's key in its own
:func:`key_header_for` header.

This module is the hosted half: where the keys and the choices are read from,
what refusals read like, and the two limits. It holds no key of its own --
:func:`Hosted.request_llms` returns a
:class:`~nl2sql.llm.request_key.RequestLLMs` to the caller, which passes it to
:func:`nl2sql.llm.request_key.use_request_llms` for the length of one question
and never anywhere else.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, Response

from nl2sql.llm.providers import (
    KEY_SHAPE_MESSAGE,
    KEYED_PROVIDERS,
    LLM_AGENTS,
    VERIFIED_MODELS,
    looks_like_api_key,
)
from nl2sql.llm.request_key import RequestLLMs

# The header the page sends its key in. A header, not the body: a body is what
# request logs, validation errors and error reports quote.
#
# One key per provider goes in a header of its own, ``KEY_HEADER-<provider>``
# (``X-NL2SQL-Api-Key-anthropic``), so a key never shares a header with
# anything else: no parser, no error message and no quoting rule can expose
# one. ``KEY_HEADER`` on its own is the key sent without naming a provider,
# which is the one-key case and what the header has always meant.
KEY_HEADER = "X-NL2SQL-Api-Key"

# The model each step is to run on: one compact JSON object, agent name ->
# "provider:model". It carries no secret, so it is safe to parse and safe to
# quote back in a refusal.
MODELS_HEADER = "X-NL2SQL-Models"

# The agent names a step may be chosen for. The labels are the settings
# panel's, so a refusal names a step the way the page does.
STEP_AGENTS: Tuple[str, ...] = tuple(LLM_AGENTS.values())


def step_label(agent: str) -> str:
    """What the page calls a step, for a message a visitor reads."""
    from nl2sql.cli.demo.playground.settings import LLM_NODES

    return next((node["label"] for node in LLM_NODES if node["agent"] == agent), agent)


def provider_label(provider: str) -> str:
    from nl2sql.cli.demo.playground.settings import PROVIDER_LABELS

    return PROVIDER_LABELS.get(provider, provider)


def key_header_for(provider: str) -> str:
    """The header one provider's key travels in."""
    return f"{KEY_HEADER}-{provider}"


MODELS_SHAPE_MESSAGE = (
    f"{MODELS_HEADER} must be a JSON object of step name to \"provider:model\"."
)

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


def _models_from(raw: Optional[str]) -> Dict[str, Tuple[str, str]]:
    """The model header as ``{agent: (provider, model)}``, or a 400 saying why not.

    Every name is checked against what the engine knows -- the pipeline's own
    steps and the verified model lists -- so nothing arbitrary reaches a client.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=MODELS_SHAPE_MESSAGE)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=MODELS_SHAPE_MESSAGE)

    chosen: Dict[str, Tuple[str, str]] = {}
    for agent, value in parsed.items():
        if agent not in STEP_AGENTS:
            raise HTTPException(status_code=400,
                                detail=f"{_quotable(agent)} is not a step of the pipeline.")
        if value is None or value == "":
            continue  # the default: the same as sending nothing for this step
        if not isinstance(value, str) or ":" not in value:
            raise HTTPException(status_code=400, detail=MODELS_SHAPE_MESSAGE)
        provider, model = value.split(":", 1)
        verified = VERIFIED_MODELS.get(provider)
        if not verified:
            raise HTTPException(
                status_code=400,
                detail=f"A step can be put on {' or '.join(provider_label(p) for p in VERIFIED_MODELS)} "
                       f"for now, not {_quotable(provider)}.")
        if model not in verified:
            raise HTTPException(
                status_code=400,
                detail=f"{_quotable(model)} is not on the verified list for {provider_label(provider)}: "
                       f"{', '.join(verified)}.")
        chosen[agent] = (provider, model)
    return chosen


def _quotable(value: object) -> str:
    """``value`` as a refusal may repeat it, and never something shaped like a key.

    The model header holds no secret when the page fills it, but a person
    driving the API by hand could paste a key into it, and a refusal is the one
    place this server would otherwise echo what it was sent.
    """
    text = str(value)
    return "that value" if looks_like_api_key(text) else f"'{text}'"


def _require_keys(llms: RequestLLMs, default_provider: str) -> None:
    """Refuses, naming the step and the provider, when a step has no key.

    Checked before the question runs, so a visitor is told which step to fix
    rather than watching the run fail somewhere inside the graph.
    """
    from nl2sql.llm.request_key import MissingProviderKey

    for agent in STEP_AGENTS:
        try:
            llms.resolve(agent, default_provider)
        except MissingProviderKey as missing:
            label = provider_label(missing.provider)
            raise HTTPException(
                status_code=400,
                detail=(f"The {step_label(missing.agent)} step is set to run on {label}, but no "
                        f"{label} key was supplied. Add one under Settings, or put that step back "
                        f"on a provider you have a key for."),
            ) from None


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

    def request_llms(self, request: Request, default_provider: str = "openai") -> RequestLLMs:
        """The keys and the per-step models this request brought.

        One header per provider carries a key; one compact JSON header carries
        the model each step is to run on. Nothing here quotes a key back: a
        malformed one is refused by shape alone, and the model header, which
        holds no secret, is the only thing a message ever repeats.

        Raises:
            HTTPException: 401 with no key at all, 400 for a key that is not
            one, for a model choice that is not on the verified list, or for a
            step whose chosen provider has no key here.
        """
        keys: Dict[str, str] = {}
        for provider in KEYED_PROVIDERS:
            value = (request.headers.get(key_header_for(provider)) or "").strip()
            if not value:
                continue
            if not looks_like_api_key(value):
                raise HTTPException(status_code=400, detail=KEY_SHAPE_MESSAGE)
            keys[provider] = value

        fallback = (request.headers.get(KEY_HEADER) or "").strip()
        if fallback:
            if not looks_like_api_key(fallback):
                raise HTTPException(status_code=400, detail=KEY_SHAPE_MESSAGE)
            from nl2sql.llm.providers import provider_for_key

            keys.setdefault(provider_for_key(fallback), fallback)
        # A key named for a provider is a deliberate choice, so once there is
        # more than one the un-named key stops standing in for all of them.
        if len(keys) > 1:
            fallback = ""

        models = _models_from(request.headers.get(MODELS_HEADER))
        if not keys:
            raise HTTPException(status_code=401, detail=NO_KEY_MESSAGE)

        llms = RequestLLMs(keys=keys, models=models, fallback=fallback or None)
        _require_keys(llms, default_provider)
        return llms

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
