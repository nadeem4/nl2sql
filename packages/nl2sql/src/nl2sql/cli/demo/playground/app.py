"""The playground FastAPI app.

Fourteen routes:

``GET  /``                     the built React page
``GET  /api/meta``             mode, dataset, the guided questions (flat, and grouped
                               by datasource), the roles and how many guided questions
                               have replay recordings
``GET  /api/schema``           the indexed schema, so a visitor sees the database first
``POST /api/ask``              one ``QueryResult``, plus a ``replay_miss`` flag; a miss
                               carries one plain error instead of the raw ones
``GET  /api/trace/{trace_id}`` one run trace, read only from the traces directory
``GET  /api/settings``         the settings panel: masked key, verified models, one model per node
``POST /api/settings/key``     save an API key to ``.env.demo`` and switch to live
``POST /api/settings/models``  write a model per LLM node into ``llm.demo.yaml``
``GET  /api/index``            index health (entries by type, schema version, when built)
                               and the state of a running rebuild
``POST /api/index/rebuild``    rebuild the datasource's snapshot and vector entries
``GET  /api/retrieval``        whether the Retrieval inspector is on, and its choices
``POST /api/retrieval``        one MMR search of the live index: the pool with scores,
                               the picks in order, and what was dropped
``GET  /api/feedback``         whether answer feedback is on, the counts and the ratings
``POST /api/feedback``         rate a run this playground answered: up or down, and a note

Hosted mode (``nl2sql demo --hosted``, see
:mod:`nl2sql.cli.demo.playground.hosted`) changes three of these: ``/api/ask``
takes the visitor's own key from a header and uses it for that one call,
``/api/settings`` reports that there is nothing to save, and
``/api/index/rebuild`` and the feedback routes are refused. Everything else is
what it is locally.

Every result pane in the browser is a renderer over ``QueryResult``; nothing
is computed here that the engine does not already return. The only state is
the settings panel's (see ``settings.py``): the current mode, and the gate that
keeps a settings change from landing under a running question; and the index
panel's (see ``index_panel.py``): the one rebuild that may be running; and the
last runs it answered, so a rating is stored with what the server returned
rather than what a page claims (see ``nl2sql.feedback``).
"""
from __future__ import annotations

import pathlib
import threading
from collections import OrderedDict
from importlib.resources import files
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nl2sql.auth.models import UserContext
from nl2sql.cli.demo.playground.hosted import FEEDBACK_MESSAGE, REBUILD_MESSAGE, Hosted
from nl2sql.cli.demo.playground.index_panel import IndexPanel
from nl2sql.cli.demo.playground.settings import SettingsPanel
from nl2sql.common.settings import settings
from nl2sql.feedback import NOTE_MAX_CHARS, FeedbackStore, run_record, run_signals
from nl2sql.llm.request_key import use_api_key
from nl2sql.tracing.document import find_trace, load_trace
from nl2sql.tracing.trace import engine_info

# The errors replay mode raises when no recording matches the question. The page
# turns these into "this question has no recording" rather than a crash report.
REPLAY_MISS_CODES = {"PLANNING_FAILURE", "MISSING_LLM"}

# An unrecorded question usually fails earlier than the planner: the replay
# server answers 400 with this marker and the pipeline reports it as
# ORCHESTRATOR_CRASH, which no error code alone distinguishes from a real crash.
REPLAY_MISS_MARKER = "fake llm: no rule for"

# What a replay miss answers instead of the replay server's internals.
REPLAY_MISS_MESSAGE = "No recorded answer for this question. Add an API key to ask it live."

STATIC_DIR = pathlib.Path(str(files("nl2sql.cli.demo.playground") / "static"))

# Shown when the page has not been built. The React source lives in
# `web/playground`; `npm run build` writes into STATIC_DIR and the result is
# committed so a pip install needs no Node.
_MISSING_PAGE = (
    "<!doctype html><html><head><title>nl2sql playground</title></head>"
    "<body><div id=\"root\">The playground page has not been built. "
    "Run <code>npm install &amp;&amp; npm run build</code> in "
    "<code>web/playground</code>.</div></body></html>"
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


class RebuildRequest(BaseModel):
    enrich: bool = False


# The entry types the index holds, in the order the inspector lists them.
ENTRY_TYPES = ["schema.datasource", "schema.table", "schema.column", "schema.relationship", "schema.metric"]


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    lambda_mult: float = Field(default=0.7, ge=0.0, le=1.0)
    types: List[str] = Field(default_factory=list)
    datasource_id: Optional[str] = None


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
    if not settings.feedback_enabled:
        return "Feedback is turned off (FEEDBACK_ENABLED=false)."
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


def _engine_version() -> str:
    info = engine_info()
    return f"{info['version']} ({info['git_sha']})" if info.get("git_sha") else str(info["version"])


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


def _read_page() -> str:
    index = STATIC_DIR / "index.html"
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return _MISSING_PAGE


def _default_datasource(engine, dataset: str) -> str:
    """The datasource the schema panel shows when the caller names none."""
    ids = list(engine.list_datasources() or [])
    return dataset if dataset in ids else (ids[0] if ids else dataset)


def _llm_configs(engine) -> Dict[str, Any]:
    """Provider, model and temperature per configured agent, for a feedback record."""
    return {name: {key: (config or {}).get(key) for key in ("provider", "model", "temperature")}
            for name, config in (engine.list_llms() or {}).items()}


def build_app(engine, questions: List[str], roles: List[str], mode: str, dataset: str,
              trace_dir: Optional[pathlib.Path] = None, project_dir: Optional[pathlib.Path] = None,
              host: str = "127.0.0.1", allow_settings: bool = False,
              recorded_questions: int = 0,
              questions_by_datasource: Optional[Dict[str, List[str]]] = None,
              hosted: Optional[Hosted] = None) -> FastAPI:
    """Builds the playground app over ``engine``.

    ``recorded_questions`` is how many of ``questions`` the loaded replay
    recordings can answer; ``/api/meta`` reports it so the page claims
    recorded answers only when there are some.

    ``questions_by_datasource`` is the same guided questions, grouped, so the
    page can label each pile instead of running three databases' worth of them
    into one list. Without it the whole list is one group under ``dataset``.

    ``project_dir``, ``host`` and ``allow_settings`` drive the settings panel:
    it is on only for a demo project, served on a loopback host or with
    ``allow_settings``; everywhere else it reports why it is off.

    ``hosted`` turns the public-demo server on (see
    :mod:`nl2sql.cli.demo.playground.hosted`): every question carries the
    visitor's own key in a header, the limits apply, and everything that would
    write to disk -- settings, rebuild, feedback -- is refused.
    """
    app = FastAPI(title="nl2sql playground")
    page = _read_page()
    hosted = hosted or Hosted(enabled=False)
    panel = SettingsPanel(engine, project_dir, mode, host, allow_settings, hosted=hosted.enabled)
    index_panel = IndexPanel(engine, panel, _default_datasource(engine, dataset), project_dir)
    app.state.settings_panel = panel
    app.state.index_panel = index_panel
    app.state.hosted = hosted
    # trace id -> what feedback would store for that run; never its rows.
    recent: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    recent_lock = threading.Lock()

    def trace_directory() -> pathlib.Path:
        return pathlib.Path(trace_dir) if trace_dir is not None else pathlib.Path(settings.trace_dir)

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
        if not body.get("trace_id") or _feedback_off_reason(panel):
            return
        record = run_record(body, question=req.question, role=req.role,
                            llm_configs=_llm_configs(engine),
                            engine_version=_engine_version())
        with recent_lock:
            recent[record["trace_id"]] = record
            recent.move_to_end(record["trace_id"])
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
    def index() -> str:
        return page

    # One group per datasource that has guided questions, in the order given.
    groups = [{"datasource": ds, "questions": list(qs)}
              for ds, qs in (questions_by_datasource or {dataset: questions}).items() if qs]

    @app.get("/api/meta")
    def meta() -> Dict[str, Any]:
        return {"mode": panel.mode, "dataset": dataset, "questions": questions,
                "question_groups": groups, "roles": roles,
                "recorded_questions": recorded_questions, **hosted.describe()}

    @app.get("/api/schema")
    def schema(datasource: Optional[str] = None) -> Dict[str, Any]:
        return engine.get_schema(datasource or _default_datasource(engine, dataset))

    @app.post("/api/ask")
    def ask(req: AskRequest, request: Request, response: Response) -> Dict[str, Any]:
        # Sync on purpose: the engine blocks, so Starlette runs this in a thread
        # instead of stalling the event loop.
        #
        # Hosted: the key arrives in a header, is bound to this call and to no
        # other, and is gone when the block ends. It is never written to a
        # file, an environment variable or a module-level cache; the registry
        # builds a client from it per request and keeps none (see
        # ``nl2sql.llm.request_key``).
        key = hosted.api_key(request) if hosted.enabled else None
        if hosted.enabled:
            hosted.spend(request, response)
        with panel.gate.run(), use_api_key(key):
            result = engine.run_query(
                req.question, execute=req.execute, user_context=UserContext(roles=[req.role])
            )
        body = result.model_dump(mode="json")
        errors = body.get("errors", [])
        codes = {e.get("error_code") for e in errors}
        missing = bool(codes & REPLAY_MISS_CODES) or any(
            REPLAY_MISS_MARKER in (e.get("message") or "") for e in errors
        )
        body["replay_miss"] = panel.mode == "replay" and missing
        if body["replay_miss"]:
            # The raw errors name the fake server and read as a crash; the
            # reasoning log and the failed call's usage entry repeat them.
            body["errors"] = [{"node": "replay", "message": REPLAY_MISS_MESSAGE,
                               "error_code": "REPLAY_MISS", "severity": "ERROR"}]
            body["reasoning"] = [r for r in body.get("reasoning", []) if REPLAY_MISS_MARKER not in str(r)]
            for call in (body.get("usage") or {}).get("calls", []):
                if REPLAY_MISS_MARKER in (call.get("error") or ""):
                    call["error"] = REPLAY_MISS_MESSAGE
        remember(req, body)
        return body

    @app.get("/api/trace/{trace_id}")
    def trace(trace_id: str) -> FileResponse:
        """One trace file. The id is validated as a plain token and the file must
        sit directly in the traces directory, so no path can lead outside it."""
        try:
            path = find_trace(trace_id, trace_directory())
        except ValueError:
            raise HTTPException(status_code=400, detail="Not a valid trace id.")
        if path is None:
            raise HTTPException(status_code=404, detail="No trace with that id.")
        return FileResponse(path, media_type="application/json", filename=path.name,
                            content_disposition_type="inline")

    @app.get("/api/feedback")
    def read_feedback(request: Request) -> Dict[str, Any]:
        reason = _feedback_off_reason(panel)
        if reason:
            return {"available": False, "reason": reason}
        panel.guard_read(request)
        path = pathlib.Path(settings.schema_store_path)
        if not path.exists():
            return {"available": True, "reason": None, "counts": {"up": 0, "down": 0}, "entries": []}
        store = FeedbackStore(path)
        try:
            return {"available": True, "reason": None, "counts": store.counts(), "entries": store.list(limit=50)}
        finally:
            store.close()

    @app.post("/api/feedback", dependencies=[Depends(feedback_guard)])
    def save_feedback(req: FeedbackRequest) -> Dict[str, Any]:
        """Rates a run this playground answered. What is stored comes from the
        server's own record of the run; the page sends only the id, the rating
        and the note."""
        with recent_lock:
            record = dict(recent[req.trace_id]) if req.trace_id in recent else None
        if record is None:
            raise HTTPException(status_code=404,
                                detail="Ask a question first: feedback rates a run this playground answered.")
        try:
            path = find_trace(req.trace_id, trace_directory())
            if path is not None:
                # The trace also sees validator rejections a retry fixed.
                doc = load_trace(path)
                record.update(run_signals(doc.get("result") or {}, doc))
                if "SECURITY_VIOLATION" in record["error_codes"]:
                    record["sql"] = []
        except (ValueError, OSError):
            pass  # the run's own signals stand
        store = FeedbackStore(pathlib.Path(settings.schema_store_path))
        try:
            saved = store.save(record, rating=req.rating, note=req.note)
            counts = store.counts()
        finally:
            store.close()
        return {"saved": saved, "counts": counts}

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
    def rebuild(req: RebuildRequest) -> Dict[str, Any]:
        # Guarded exactly like a settings change: local only unless
        # --allow-settings, and only from the playground page itself. Hosted,
        # it is off outright -- it would write the index and the snapshot.
        index_panel.start(req.enrich)
        return index_panel.read()

    @app.get("/api/retrieval")
    def retrieval_options() -> Dict[str, Any]:
        from nl2sql.indexing.vector_store import VectorStore

        reason = _retrieval_off_reason(panel)
        return {
            # Hosted, the panel is off but the inspector is on, so the
            # inspector's own reason is the one that decides.
            "available": reason is None,
            "reason": reason,
            "datasource_id": index_panel.datasource_id,
            "datasources": [d["datasource_id"] for d in index_panel.health().get("datasources", [])],
            "types": ENTRY_TYPES,
            "defaults": {"k": 8, "lambda_mult": VectorStore.LAMBDA_MULT,
                         "fetch_multiplier": VectorStore.FETCH_MULTIPLIER},
        }

    @app.post("/api/retrieval", dependencies=[Depends(retrieval_guard)])
    def retrieval(req: RetrievalRequest) -> Dict[str, Any]:
        """One MMR search of the live index, as the engine runs it. It reads index
        metadata only; the embedder is local, so it costs nothing."""
        unknown = sorted(set(req.types) - set(ENTRY_TYPES))
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown entry types: {', '.join(unknown)}.")
        try:
            return engine.inspect_retrieval(req.query, k=req.k, lambda_mult=req.lambda_mult,
                                            types=req.types, datasource_id=req.datasource_id or None)
        except Exception as exc:  # no index, a model mismatch, an unreadable collection
            raise HTTPException(status_code=409, detail=str(exc))

    return app
