"""The playground FastAPI app.

Twenty-one routes:

``GET  /``                     the built React page, with the link preview's
                               tags in its head (see :mod:`preview`)
``GET  /social-card.png``      the 1200x630 card those tags name
``GET  /clips/{name}``         the home page's clips: a webm or poster jpg per feature and theme
``GET  /api/health``           ``{"status": "ok", "version": <nl2sql-engine version>}``,
                               from process state only; the release pipeline
                               polls it on the Space for the version it published
``GET  /api/meta``             mode, dataset, every registered datasource, the guided
                               questions (flat, and grouped by datasource), the roles
                               and how many guided questions have replay recordings,
                               and the provider and model those were recorded with
``GET  /api/schema``           the indexed schema of one datasource (``?datasource=``,
                               default the demo's own), so a visitor sees the database
                               first and can look at each of them; only the tables
                               ``?role=`` may read (required: 400 without it)
``POST /api/ask``              one ``QueryResult``, plus ``replay_miss`` and ``recorded``
                               (answered from recordings, not run live just now); a miss
                               carries one plain error instead of the raw ones; a client
                               that disconnects (the page's Stop) cancels the run
``POST /api/ask/stream``       the same question as Server-Sent Events: each step as it
                               starts and ends, the result manifest, the answer as it is
                               written, then ``final`` (what ``/api/ask`` returns) or
                               ``error``; the same refusals, before the stream starts
``GET  /api/trace/{trace_id}`` one run trace (``engine.traces.get``), read only from the
                               engine's traces folder
``GET  /api/query/{request_id}/results/{result_id}``
                               one page of a run's saved result (``offset``, ``limit``
                               capped at 1,000), as ``nl2sql-api`` serves it
``GET  /api/query/{request_id}/results/{result_id}.csv``
                               the whole saved result as CSV, streamed; both read the
                               engine's own tenant folder only, as the role the run was
                               asked as (``?role=``, required: 400 without it), so an
                               unknown, foreign or crafted id, or another role's run,
                               is a 404
``GET  /api/pipeline``         every step of a run in order, which call a model, and the
                               model each of those is configured to use (``engine.about()``),
                               and the model the replay recordings were made with
``GET  /api/settings``         the settings panel: masked key, verified models, one model per node
``POST /api/settings/key``     save an API key to ``.env`` and switch to live
``POST /api/settings/models``  write a model per LLM node into ``llm.yaml``
``GET  /api/index``            index health (entries by type, schema version, when built)
                               and the state of a running rebuild
``POST /api/index/rebuild``    rebuild the datasource's snapshot and vector entries
``GET  /api/retrieval``        whether the Retrieval inspector is on, and its choices
                               (``engine.index.inspect_defaults()``)
``POST /api/retrieval``        one MMR search of the live index: the pool with scores,
                               the picks in order, and what was dropped, as the
                               request's ``role`` (only what that role may read)
``GET  /api/feedback``         whether answer feedback is on, the counts and the ratings
``POST /api/feedback``         rate a run this playground answered: up or down, and a note
                               (``engine.feedback``)

Hosted mode (``nl2sql demo --hosted``, see
:mod:`nl2sql.cli.demo.playground.hosted`) changes these: ``/api/ask`` (and
``/api/ask/stream``, by the same rules) takes the visitor's own key from a header and uses it for that one call, or,
with no key, replays a recorded guided question and answers anything else
with a replay miss that asks for a key,
``/api/settings`` reports that there is nothing to save, and
``/api/index/rebuild`` and the feedback routes are refused. Everything else is
what it is locally.

Every result pane in the browser is a renderer over ``QueryResult``; nothing
is computed here that the engine does not already return. The only state is
the settings panel's (see ``settings.py``): the current mode, and the gate that
keeps a settings change from landing under a running question; and the index
panel's (see ``index_panel.py``): the one rebuild that may be running; and the
last runs it answered, so a rating is stored with what the server returned
rather than what a page claims (``engine.feedback.record``).

It is a client of the facade like any other: everything it reads from the
engine is a public method or namespace (a boundary test holds it to that).
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import threading
from datetime import datetime, timezone
from collections import OrderedDict
from importlib.resources import files
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from nl2sql import CancellationToken, Event, QueryResult, ResultNotFound, UserContext

from nl2sql.cli.demo.playground.hosted import FEEDBACK_MESSAGE, REBUILD_MESSAGE, Hosted, Replay
from nl2sql.cli.demo.playground.index_panel import IndexPanel
from nl2sql.cli.demo.playground.preview import CARD, CARD_ROUTE, with_preview
from nl2sql.cli.demo.playground.settings import SettingsPanel
from nl2sql.cli.demo.stamp import engine_version
from nl2sql.llm.request_key import use_request_llms

# The errors replay mode raises when no recording matches the question. The page
# turns these into "this question has no recording" rather than a crash report.
REPLAY_MISS_CODES = {"PLANNING_FAILURE", "MISSING_LLM"}

# An unrecorded question usually fails earlier than the planner: the replay
# server answers 400 with this marker and the pipeline reports it as
# ORCHESTRATOR_CRASH, which no error code alone distinguishes from a real crash.
REPLAY_MISS_MARKER = "fake llm: no rule for"

# What a replay miss answers instead of the replay server's internals.
REPLAY_MISS_MESSAGE = "No recorded answer for this question. Add an API key to ask it live."

REPLAY_MISS_ERROR = {"node": "replay", "message": REPLAY_MISS_MESSAGE, "error_code": "REPLAY_MISS",
                     "severity": "ERROR", "provider": None, "provider_response": None}


def _replay_miss() -> Dict[str, Any]:
    """A run that never started because nothing recorded can answer it."""
    body = QueryResult(status="error", errors=[dict(REPLAY_MISS_ERROR)]).model_dump(mode="json")
    body.update(replay_miss=True, recorded=False)
    return body


def _run_event(name: str, data: Dict[str, Any]) -> Event:
    """A run's terminal event the page makes itself: a replay miss that never ran, or a broken run."""
    return Event(name=name, at=datetime.now(timezone.utc), data=data)


# A proxy (the Space's) buffers a response unless told not to, which would hold
# every event back until the run ends.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _event_stream(chunks, cookies) -> StreamingResponse:
    """A ``text/event-stream`` response carrying ``cookies`` (the hosted session's)."""
    stream = StreamingResponse(chunks, media_type="text/event-stream", headers=SSE_HEADERS)
    stream.raw_headers.extend(cookies)
    return stream


# How often a running question checks that its browser is still waiting.
DISCONNECT_POLL_SECONDS = 0.25

STATIC_DIR = pathlib.Path(str(files("nl2sql.cli.demo.playground") / "static"))

# The home page's clips: a light and a dark webm and poster per feature,
# recorded by scripts/record_home_clips.py. They sit beside the page rather
# than inside it, so the bundle does not carry megabytes of video, and outside
# `static/`, which `npm run build` empties.
CLIPS_DIR = pathlib.Path(str(files("nl2sql.cli.demo.playground") / "assets" / "clips"))
CLIP_NAME = re.compile(r"^[a-z]+(-dark)?\.(webm|jpg)$")
CLIP_TYPES = {"webm": "video/webm", "jpg": "image/jpeg"}

# The long version of what the Pipeline page summarises. The playground is
# served from a pip install, so a repository-relative path would not resolve.
PIPELINE_DOCS = "https://github.com/nadeem4/nl2sql/blob/main/docs/architecture/pipeline.md"

# Shown when the page has not been built. The page in STATIC_DIR is the old
# single-file build, committed so a pip install needs no Node; its source moved
# to `nl2sql-ui` (D0b), whose build now goes to nl2sql-api, so this folder is
# no longer rebuilt until X1b deletes this server.
_MISSING_PAGE = (
    "<!doctype html><html><head><title>nl2sql playground</title></head>"
    "<body><div id=\"root\">The playground page has not been built. "
    "Run <code>npm install &amp;&amp; npm run build</code> in "
    "<code>nl2sql-ui</code>.</div></body></html>"
)


class AskRequest(BaseModel):
    question: str
    role: str = "admin"
    execute: bool = True


class KeyRequest(BaseModel):
    api_key: str


class NodeChoice(BaseModel):
    provider: str
    model: str


class ModelsRequest(BaseModel):
    # Per node: None (the default agent), a model on the default's provider,
    # or a provider and a model.
    models: Dict[str, Union[NodeChoice, str, None]]


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    lambda_mult: float = Field(default=0.7, ge=0.0, le=1.0)
    types: List[str] = Field(default_factory=list)
    datasource_id: Optional[str] = None
    # The role the search is made as, required like /api/schema's (D33: no default view): the pool holds only
    # what that role may read.
    role: str


# The longest note a rating keeps (the feedback table keeps no more).
NOTE_MAX_CHARS = 280


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    rating: Literal["up", "down"]
    note: Optional[str] = Field(default=None, max_length=NOTE_MAX_CHARS)


# How many answered runs the playground remembers for rating.
RECENT_RUNS = 200


def _feedback_off_reason(panel) -> Optional[str]:
    """Why answer feedback is off, in its own words; the gate is the settings panel's."""
    if panel.hosted:
        return FEEDBACK_MESSAGE
    if not panel.engine.config.settings.feedback.enabled:
        return "Feedback is turned off (settings.feedback.enabled is false)."
    if panel.available:
        return None
    if panel.project_dir is None:
        return "Feedback is available in the playground that nl2sql demo starts."
    return (
        f"This playground is bound to {panel.host}, which other machines can reach, and it has no "
        "login. Feedback keeps the question and SQL of every rated run, and anyone who can open this "
        "page could read or add to it. Restart it on 127.0.0.1, or pass --allow-settings if you trust "
        "everyone who can reach it."
    )


def _retrieval_off_reason(panel) -> Optional[str]:
    """Why the inspector is off, in its own words; the gate is the settings panel's."""
    if panel.available or panel.hosted:
        # Hosted: the index holds our own sample schema and nothing else, and
        # the inspector only reads it, so it is part of what the demo shows.
        return None
    if panel.project_dir is None:
        return "The Retrieval inspector is available in the playground that nl2sql demo starts."
    return (
        f"This playground is bound to {panel.host}, which other machines can reach, and it has no "
        "login. The inspector shows every index entry, including column statistics and sample "
        "values, to anyone who can open this page. Restart it on 127.0.0.1, or pass "
        "--allow-settings if you trust everyone who can reach it."
    )


def _counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many ratings are up and how many down."""
    return {rating: sum(1 for r in rows if r.get("rating") == rating) for rating in ("up", "down")}


def _read_page() -> str:
    index = STATIC_DIR / "index.html"
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return _MISSING_PAGE


def _available(engine) -> List[str]:
    """The datasources questions can reach: every configured one that has not failed."""
    return [d.id for d in engine.datasources.list() if d.status != "failed"]


def _default_datasource(engine, dataset: str) -> str:
    """The datasource the schema panel shows when the caller names none."""
    ids = _available(engine)
    return dataset if dataset in ids else (ids[0] if ids else dataset)


def _datasource_ids(engine, dataset: str) -> List[str]:
    """Every registered datasource, the one the schema panel opens on first.

    The page offers these as the schema panel's choices, so the order is the
    order they are listed in, and the default leads.
    """
    ids = _available(engine)
    if dataset in ids:
        return [dataset] + [d for d in ids if d != dataset]
    return ids or [dataset]


# What a remembered run keeps of its result: what a rating stores, and no rows.
_RATED_FIELDS = ("trace_id", "status", "errors", "source", "usage")
_RATED_SUB_QUERY_FIELDS = ("sql", "datasource_id", "retry_count", "plan_source", "validation")


def _rated_part(body: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a run's result ``engine.feedback.record`` reads; never its rows or answer."""
    kept = {key: body.get(key) for key in _RATED_FIELDS}
    kept["sub_queries"] = [{key: s.get(key) for key in _RATED_SUB_QUERY_FIELDS}
                           for s in body.get("sub_queries") or [] if isinstance(s, dict)]
    return kept


async def cancel_when_disconnected(request: Request, token: CancellationToken,
                                   interval: float = DISCONNECT_POLL_SECONDS) -> None:
    """Cancels ``token`` once the client that asked has gone.

    The page's Stop aborts its fetch, which closes the connection; the run
    then stops before its next step or model call (see
    :class:`nl2sql.CancellationToken`). The step in flight finishes.
    """
    while not token.is_cancelled():
        if await request.is_disconnected():
            token.cancel()
            return
        await asyncio.sleep(interval)


def build_app(engine, questions: List[str], roles: List[str], mode: str, dataset: str,
              project_dir: Optional[pathlib.Path] = None,
              host: str = "127.0.0.1", allow_settings: bool = False,
              recorded_questions: int = 0,
              questions_by_datasource: Optional[Dict[str, List[str]]] = None,
              hosted: Optional[Hosted] = None,
              replay: Optional[Replay] = None,
              api_keys: Optional[Dict[str, str]] = None) -> FastAPI:
    """Builds the playground app over ``engine``.

    ``recorded_questions`` is how many of ``questions`` the loaded replay
    recordings can answer; ``/api/meta`` reports it so the page claims
    recorded answers only when there are some.

    ``replay`` is local replay mode's recordings (hosted mode carries its own
    on ``hosted``): while the playground is in replay mode each run is asked
    under the model they were made with. Either way ``/api/meta`` and
    ``/api/pipeline`` report that ``recorded_with``, so a page answering from
    recordings names that model, marked recorded, instead of the one the
    config falls back to.

    ``questions_by_datasource`` is the same guided questions, grouped, so the
    page can label each pile instead of running three databases' worth of them
    into one list. Without it the whole list is one group under ``dataset``.

    ``project_dir``, ``host`` and ``allow_settings`` drive the settings panel:
    it is on only for a demo project, served on a loopback host or with
    ``allow_settings``; everywhere else it reports why it is off.

    ``api_keys`` (provider -> key) are the keys ``nl2sql demo`` gave the
    engine as values rather than through the environment (``--api-key``).
    The settings panel shows them masked, adds the keys saved in it, and
    passes them all on each time it reloads the engine's LLM.

    ``hosted`` turns the public-demo server on (see
    :mod:`nl2sql.cli.demo.playground.hosted`): every question carries the
    visitor's own key in a header, the limits apply, and everything that would
    write to disk -- settings, rebuild, feedback -- is refused.
    """
    app = FastAPI(title="nl2sql playground")
    page = _read_page()
    hosted = hosted or Hosted(enabled=False)
    recordings = replay or hosted.replay
    recorded_with = recordings.recorded_with if recordings is not None else None
    panel = SettingsPanel(engine, project_dir, mode, host, allow_settings, hosted=hosted.enabled,
                          api_keys=api_keys)
    index_panel = IndexPanel(engine, panel, _default_datasource(engine, dataset), project_dir)
    app.state.settings_panel = panel
    app.state.index_panel = index_panel
    app.state.hosted = hosted
    # trace id -> the run as a rating would store it: question, role, result; never its rows.
    recent: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    recent_lock = threading.Lock()

    def feedback_guard(request: Request) -> None:
        # Local only, exactly like Settings and Rebuild.
        reason = _feedback_off_reason(panel)
        if reason:
            raise HTTPException(status_code=403, detail=reason)
        panel.guard(request)

    def rebuild_guard(request: Request) -> None:
        # Hosted: off outright, and said in the rebuild's own words rather
        # than the settings panel's.
        if hosted.enabled:
            raise HTTPException(status_code=403, detail=REBUILD_MESSAGE)
        panel.guard(request)

    def remember(req: AskRequest, body: Dict[str, Any]) -> None:
        trace_id = body.get("trace_id")
        if not trace_id or _feedback_off_reason(panel):
            return
        run = {"result": _rated_part(body), "question": req.question, "role": req.role}
        with recent_lock:
            recent[trace_id] = run
            recent.move_to_end(trace_id)
            while len(recent) > RECENT_RUNS:
                recent.popitem(last=False)

    def retrieval_guard(request: Request) -> None:
        # Local only, exactly like Settings and Rebuild: the same guard, with
        # the inspector's own reason when it is off. Hosted, the inspector is
        # part of the demo -- it reads our own sample index and writes
        # nothing -- so it answers everyone, at the hosted pace.
        reason = _retrieval_off_reason(panel)
        if reason:
            raise HTTPException(status_code=403, detail=reason)
        if hosted.enabled:
            hosted.throttle(request)
            return
        panel.guard(request)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI echoes the offending input by default; for the key route that
        # input is the key. Keep where and why, drop what.
        detail = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    if STATIC_DIR.is_dir():
        # Harmless when the build inlined everything; needed when it did not.
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> str:
        # The preview tags are added here rather than baked into the built
        # page: they carry absolute URLs, and the host they name is the one
        # this request arrived on. See :mod:`preview`.
        return with_preview(page, request)

    @app.get(CARD_ROUTE, include_in_schema=False)
    def social_card() -> FileResponse:
        """The card the preview tags name. It ships with the package."""
        if not CARD.is_file():
            raise HTTPException(status_code=404, detail="No preview card in this install.")
        return FileResponse(CARD, media_type="image/png")

    @app.get("/clips/{name}", include_in_schema=False)
    def clip(name: str) -> FileResponse:
        """One of the home page's clips. The name is a plain token, so no path
        can lead outside the clips folder."""
        path = CLIPS_DIR / name
        if not CLIP_NAME.match(name) or not path.is_file():
            raise HTTPException(status_code=404, detail="No such clip.")
        return FileResponse(path, media_type=CLIP_TYPES[name.rsplit(".", 1)[1]])

    # One group per datasource that has guided questions, in the order given.
    groups = [{"datasource": ds, "questions": list(qs)}
              for ds, qs in (questions_by_datasource or {dataset: questions}).items() if qs]

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        # Process state only, so it answers in every mode and never waits on
        # the engine. The release pipeline polls it on the live Space until
        # `version` is the one it just published.
        return {"status": "ok", "version": engine_version()}

    @app.get("/api/meta")
    def meta() -> Dict[str, Any]:
        return {"mode": panel.mode, "dataset": dataset, "questions": questions,
                "question_groups": groups, "roles": roles,
                # Every registered database, not only the ones with guided
                # questions: the schema panel offers these as its choices.
                "datasources": _datasource_ids(engine, dataset),
                "recorded_questions": recorded_questions, "recorded_with": recorded_with,
                **hosted.describe()}

    @app.get("/api/schema")
    def schema(datasource: Optional[str] = None, role: Optional[str] = None) -> Dict[str, Any]:
        # No default view (D33): the page always sends its Ask as role.
        if not role:
            raise HTTPException(status_code=400, detail="Name the role to show the schema for: ?role=<role>.")
        return engine.datasources.schema(datasource or _default_datasource(engine, dataset),
                                         user_context=UserContext(roles=[role]))

    def start_question(req: AskRequest, request: Request, response: Response):
        """``(llms, replaying, miss)`` for a question about to run, or a refusal.

        Shared by ``/api/ask`` and ``/api/ask/stream``, so both apply the
        same rules. Hosted: the keys arrive one header per provider, and the
        model each step is to run on in one compact JSON header. Both are
        bound to this call and to no other, and are gone when the block
        ends. A key is never written to a file, an environment variable or a
        module-level cache; the registry builds each step's client from it
        per request and keeps none (see ``nl2sql.llm.request_key``). A
        question is charged against the hosted limits here, before it runs,
        so a stopped one still counts.

        Hosted, without a key: a guided question with a recording replays it,
        paced like any request but never counted against the session, since
        it spends nothing; anything else is ``miss``, a replay miss that asks
        for a key, and runs nothing.

        Raises:
            HTTPException: a hosted refusal (no key, a bad key, a limit reached).
        """
        replaying = panel.mode == "replay"
        # Local replay: the config already points at the replay server; this
        # asks it for the recorded model, so the run reports that model.
        llms = replay.llms() if (replaying and replay is not None) else None
        if hosted.enabled and not hosted.brings_key(request):
            hosted.throttle(request)
            if not hosted.replays(req.question):
                return None, False, _replay_miss()
            llms = hosted.replay.llms()
            replaying = True
        elif hosted.enabled:
            llms = hosted.request_llms(request, panel.default_provider)
            hosted.spend(request, response)
        return llms, replaying, None

    def finish_question(req: AskRequest, body: Dict[str, Any], replaying: bool) -> Dict[str, Any]:
        """``QueryResult`` JSON as the page reads it: plus ``replay_miss`` and ``recorded``."""
        errors = body.get("errors", [])
        codes = {e.get("error_code") for e in errors}
        missing = bool(codes & REPLAY_MISS_CODES) or any(
            REPLAY_MISS_MARKER in (e.get("message") or "") for e in errors
        )
        body["replay_miss"] = replaying and missing
        # Answered from recordings rather than by a model just now: the page
        # labels it so, and nobody mistakes it for a live run.
        body["recorded"] = replaying and not missing
        if body["replay_miss"]:
            # The raw errors name the fake server and read as a crash; the
            # reasoning log and the failed call's usage entry repeat them.
            body["errors"] = [dict(REPLAY_MISS_ERROR)]
            body["reasoning"] = [r for r in body.get("reasoning", []) if REPLAY_MISS_MARKER not in str(r)]
            for call in (body.get("usage") or {}).get("calls", []):
                if REPLAY_MISS_MARKER in (call.get("error") or ""):
                    call["error"] = REPLAY_MISS_MESSAGE
        remember(req, body)
        return body

    @app.post("/api/ask")
    async def ask(req: AskRequest, request: Request, response: Response) -> Dict[str, Any]:
        # The engine blocks, so the question runs in Starlette's threadpool;
        # the event loop meanwhile watches the connection, and a client that
        # goes away (the page's Stop) cancels the run.
        llms, replaying, miss = start_question(req, request, response)
        if miss is not None:
            return miss
        token = CancellationToken()

        def run():
            with panel.gate.run(), use_request_llms(llms):
                return engine.run_query(
                    req.question, execute=req.execute, user_context=UserContext(roles=[req.role]),
                    cancellation_token=token,
                )

        watcher = asyncio.ensure_future(cancel_when_disconnected(request, token))
        try:
            result = await run_in_threadpool(run)
        finally:
            watcher.cancel()
        return finish_question(req, result.model_dump(mode="json"), replaying)

    @app.post("/api/ask/stream")
    async def ask_stream(req: AskRequest, request: Request, response: Response) -> StreamingResponse:
        # `/api/ask`, reported as it runs (``engine.stream_query``'s events):
        # each step as it starts and ends, the result manifest, the answer as
        # it is written, then `run.finished`, whose `data.result` is exactly
        # what `/api/ask` returns, or `run.failed`. A refusal comes before
        # the stream, with the same status and sentence as `/api/ask`. The
        # run's thread hands each event to
        # the event loop; a client that goes away (the page's Stop) ends the
        # response, which cancels the run.
        llms, replaying, miss = start_question(req, request, response)
        cookies = [(k, v) for k, v in response.headers.raw if k.lower() == b"set-cookie"]
        if miss is not None:
            return _event_stream(iter([_run_event("run.finished", {"status": "error", "result": miss}).sse()]),
                                 cookies)
        token = CancellationToken()
        loop = asyncio.get_running_loop()
        events: "asyncio.Queue[Optional[Event]]" = asyncio.Queue()

        def hand_over(event: Optional[Event]) -> None:
            try:
                loop.call_soon_threadsafe(events.put_nowait, event)
            except RuntimeError:  # the server is shutting down; nobody is reading
                pass

        def run() -> None:
            try:
                with panel.gate.run(), use_request_llms(llms):
                    for event in engine.stream_query(
                        req.question, execute=req.execute, user_context=UserContext(roles=[req.role]),
                        cancellation_token=token,
                    ):
                        hand_over(event)
            except Exception as exc:
                hand_over(_run_event("run.failed", {
                    "message": f"The question could not be answered: {exc}", "error_code": "UNKNOWN_ERROR"}))
            finally:
                hand_over(None)

        async def body():
            asyncio.ensure_future(run_in_threadpool(run))
            try:
                while (event := await events.get()) is not None:
                    if event.name == "run.finished":
                        event = event.model_copy(update={"data": {
                            **event.data, "result": finish_question(req, event.data["result"], replaying)}})
                    yield event.sse()
            finally:
                # The client went away, or the run is over: stop it before its next step.
                token.cancel()

        return _event_stream(body(), cookies)

    @app.get("/api/trace/{trace_id}")
    def trace(trace_id: str) -> Dict[str, Any]:
        """One trace document. ``engine.traces.get`` validates the id as a plain
        token and reads only the engine's traces folder, so no path can lead outside it."""
        try:
            doc = engine.traces.get(trace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Not a valid trace id.")
        if doc is None:
            raise HTTPException(status_code=404, detail="No trace with that id.")
        return doc

    def _result_reader(role: Optional[str]) -> UserContext:
        # Every run here is asked anonymously as one role, so it is read back as that role (``?role=``, as the page
        # sends it): another role's run is a 404. No default role, as ``/api/schema`` has none.
        if not role:
            raise HTTPException(status_code=400, detail="Name the role the run was asked as: ?role=<role>.")
        return UserContext(roles=[role])

    # The CSV route first: ``{result_id}`` would otherwise match ``x.csv``.
    @app.get("/api/query/{request_id}/results/{result_id}.csv")
    def result_csv(request_id: str, result_id: str, role: Optional[str] = None) -> StreamingResponse:
        """The whole saved result as CSV, streamed from its parquet file. Read as the
        anonymous caller with ``role``, as the run was asked; an unknown, foreign or
        crafted id, or another role's run, is a 404."""
        user = _result_reader(role)
        try:
            chunks = engine.results.csv(request_id, result_id, user_context=user)
        except ResultNotFound:
            raise HTTPException(status_code=404, detail="No such result.")
        return StreamingResponse(chunks, media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{request_id}_{result_id}.csv"'})

    @app.get("/api/query/{request_id}/results/{result_id}")
    def result_page(request_id: str, result_id: str, role: Optional[str] = None,
                    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1)) -> Dict[str, Any]:
        """One page of a saved result (``limit`` capped at 1,000), as ``nl2sql-api`` serves it,
        read as the anonymous caller with ``role``."""
        user = _result_reader(role)
        try:
            page = engine.results.page(request_id, result_id, offset=offset, limit=limit, user_context=user)
        except ResultNotFound:
            raise HTTPException(status_code=404, detail="No such result.")
        return page.model_dump(mode="json")

    @app.get("/api/feedback")
    def read_feedback(request: Request) -> Dict[str, Any]:
        reason = _feedback_off_reason(panel)
        if reason:
            return {"available": False, "reason": reason}
        panel.guard_read(request)
        rows = engine.feedback.list()
        return {"available": True, "reason": None, "counts": _counts(rows), "entries": rows[:50]}

    @app.post("/api/feedback", dependencies=[Depends(feedback_guard)])
    def save_feedback(req: FeedbackRequest) -> Dict[str, Any]:
        """Rates a run this playground answered. What is stored comes from the
        server's own record of the run; the page sends only the id, the rating
        and the note."""
        with recent_lock:
            run = recent.get(req.trace_id)
        if run is None:
            raise HTTPException(status_code=404,
                                detail="Ask a question first: feedback rates a run this playground answered.")
        # The engine reads the run's trace too: it sees validator rejections a retry fixed.
        saved = engine.feedback.record(run["result"], question=run["question"], role=run["role"],
                                       rating=req.rating, note=req.note)
        return {"saved": saved, "counts": _counts(engine.feedback.list())}

    @app.get("/api/pipeline")
    def pipeline() -> Dict[str, Any]:
        """Every step of a run, and the model each model step is configured on.

        Read from ``engine.about()``, whose steps a test holds against the
        graphs themselves, so the page cannot describe a pipeline that is not
        the one running. Only a provider and a model come with each step;
        never a key.

        ``recorded_with`` names the model the replay recordings were made
        with. The steps keep the configured models, which a run with a key
        uses; the page shows the recorded one, marked, while it answers from
        recordings.
        """
        return {"steps": engine.about()["steps"], "docs": PIPELINE_DOCS, "recorded_with": recorded_with}

    @app.get("/api/settings")
    def read_settings() -> Dict[str, Any]:
        return panel.read()

    @app.post("/api/settings/key", dependencies=[Depends(panel.guard)])
    def save_key(req: KeyRequest) -> Dict[str, Any]:
        # Sync, like /api/ask: the save waits for running questions to finish.
        panel.save_key(req.api_key)
        return panel.read()

    @app.post("/api/settings/models", dependencies=[Depends(panel.guard)])
    def save_models(req: ModelsRequest) -> Dict[str, Any]:
        panel.set_models({agent: value.model_dump() if isinstance(value, NodeChoice) else value
                          for agent, value in req.models.items()})
        return panel.read()

    @app.get("/api/index")
    def read_index() -> Dict[str, Any]:
        return index_panel.read()

    @app.post("/api/index/rebuild", status_code=202, dependencies=[Depends(rebuild_guard)])
    def rebuild() -> Dict[str, Any]:
        # Guarded exactly like a settings change: local only unless
        # --allow-settings, and only from the playground page itself. Hosted,
        # it is off outright -- it would write the index and the snapshot.
        index_panel.start()
        return index_panel.read()

    @app.get("/api/retrieval")
    def retrieval_options() -> Dict[str, Any]:
        reason = _retrieval_off_reason(panel)
        defaults = engine.index.inspect_defaults()
        return {
            # Hosted, the panel is off but the inspector is on, so the
            # inspector's own reason is the one that decides.
            "available": reason is None,
            "reason": reason,
            "datasource_id": index_panel.datasource_id,
            "datasources": [d["datasource_id"] for d in index_panel.health().get("datasources", [])],
            "types": defaults.pop("types"),
            "defaults": defaults,
        }

    @app.post("/api/retrieval", dependencies=[Depends(retrieval_guard)])
    def retrieval(req: RetrievalRequest) -> Dict[str, Any]:
        """One MMR search of the live index, as the engine runs it. It reads index
        metadata only; the embedder is local, so it costs nothing."""
        unknown = sorted(set(req.types) - set(engine.index.inspect_defaults()["types"]))
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown entry types: {', '.join(unknown)}.")
        try:
            return engine.index.inspect(req.query, k=req.k, lambda_mult=req.lambda_mult,
                                        types=req.types, datasource_id=req.datasource_id or None,
                                        user_context=UserContext(roles=[req.role]))
        except Exception as exc:  # an unreadable index store
            raise HTTPException(status_code=409, detail=str(exc))

    return app
