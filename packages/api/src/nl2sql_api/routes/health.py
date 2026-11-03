"""``/health``, ``/ready``, ``/meta`` and ``/about``."""
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from nl2sql import CheckReport, EngineNotReady, NL2SQL, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine

router = APIRouter(tags=["health"])

Engine = Annotated[NL2SQL, Depends(get_engine)]
Visitor = Annotated[Optional[UserContext], Depends(identity.optional_caller)]

# How long `/ready` keeps the engine's check: an orchestrator polls it every few seconds, and a check connects to every
# datasource.
READY_TTL_S = 10
_lock = threading.Lock()


def forget_ready(app: FastAPI) -> None:
    """Drop the app's kept check, so the next `/ready` runs a new one."""
    with _lock:
        app.state.ready_cache = None


def _report(app: FastAPI, engine: NL2SQL) -> CheckReport:
    """``engine.check()``'s report, kept on ``app.state`` for ``READY_TTL_S``. A required datasource that fails
    raises ``EngineNotReady``, which carries the same report."""
    with _lock:
        kept: Optional[Tuple[NL2SQL, float, CheckReport]] = getattr(app.state, "ready_cache", None)
        if kept is not None and kept[0] is engine and time.monotonic() - kept[1] < READY_TTL_S:
            return kept[2]
        try:
            report = engine.check()
        except EngineNotReady as exc:
            report = exc.report
        app.state.ready_cache = (engine, time.monotonic(), report)
        return report


def _keys_optional(engine: NL2SQL) -> bool:
    """A caller brings its own key (`api.request_llm`), or every agent replays recordings and needs none."""
    return engine.config.api.request_llm or all(
        entry["provider"] == "replay" for entry in engine.llm.list().values())


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "0.0.0"


@router.get("/health", response_model=Dict[str, Any], summary="Liveness check")
def health_check():
    """Liveness, from process state only: `{"status": "ok", "version": {"engine", "api"}}`. Checks nothing else."""
    return {"status": "ok", "version": {"engine": _version("nl2sql-engine"), "api": _version("nl2sql-api")}}


@router.get("/ready", response_model=Dict[str, Any], summary="Readiness check",
            responses={503: {"description": "Not ready: `{\"status\": \"not_ready\"}`."}})
def readiness_check(request: Request, engine: Engine, user: Visitor):
    """Whether the engine can answer now, from its own `check()` (kept 10 seconds): every required datasource
    and at least one is up, every agent's key resolves (unless `api.request_llm` is on, or every agent is on
    `replay`) and the index is `ok` or `stale`. 200 `{"status": "ready"}` or 503 `{"status": "not_ready"}` to
    everyone. An admin also gets `problems`: each failing datasource and its stage, each agent whose key does not
    resolve and the index status, never a secret. An optional datasource that failed is a problem, not "not ready"."""
    report = _report(request.app, engine)
    ready = report.ready(keys_optional=_keys_optional(engine))
    body: Dict[str, Any] = {"status": "ready" if ready else "not_ready"}
    if user is not None and identity.is_admin(user, engine):
        body["problems"] = report.problems()
    return JSONResponse(body, status_code=200 if ready else 503)


@router.get("/meta", response_model=Dict[str, Any], summary="What this server is")
def meta(request: Request, engine: Engine, user: Visitor):
    """Without a caller: the `mode` (`demo`, `your_data` or `hosted`), the identity source (`auth`), `request_llm`,
    which is what a page needs to decide whether to ask for a sign-in or a key, and `start`, which screen the web app
    opens on: `"hosted"` on the hosted demo, `"choose"` while the project has no datasource and the caller is an administrator, else null. With a caller,
    also
    `caller` (`{"user_id", "admin"}`: who the caller is and whether it holds a role in `policies.admin_roles`, the
    check the admin routes make; on the hosted demo `user_id` is a one-way id of the session, never its cookie), and
    `datasources`: each one the caller may reach, with its `id`, `description`, `sample_questions` and `recorded`
    (the ones the `replay` provider can answer with no key), and `recorded_with`, the provider and model those
    recordings came from (or null). On the hosted demo, also `limits`: `questions_per_minute`,
    `questions_per_session`, `asked` (this session's count), and `jobs` and `streams` (the most at once)."""
    config = engine.config
    # `choose` (an empty project) goes only to an administrator, who is the one who can act on it; anyone else would
    # learn from it whether the project has a datasource.
    if config.mode == "hosted":
        start = "hosted"
    elif config.datasources:
        start = None
    else:
        start = "choose" if user is not None and identity.is_admin(user, engine) else None
    body: Dict[str, Any] = {"mode": config.mode, "auth": config.api.auth, "request_llm": config.api.request_llm,
                            "start": start}
    if user is None:
        return body
    hosted = getattr(request.app.state, "hosted", None)
    body["caller"] = {"admin": identity.is_admin(user, engine)}
    if user.user_id is not None:  # on the hosted demo a one-way id, never the session cookie
        body["caller"] = {"user_id": user.user_id, **body["caller"]}
    listed = engine.datasources.list(user_context=user)
    questions = [q for d in listed for q in d.sample_questions]
    recorded = set(engine.llm.recorded(questions))
    body["datasources"] = [{"id": d.id, "description": d.description, "sample_questions": list(d.sample_questions),
                            "recorded": [q for q in d.sample_questions if q in recorded]} for d in listed]
    body["recorded_with"] = engine.llm.recorded_with()
    if hosted is not None:
        body["limits"] = hosted.describe(user.user_id)
    return body


@router.get("/about", response_model=Dict[str, Any], summary="The pipeline and its models")
def about(engine: Engine):
    """`engine.about()`: the engine's version, and every step of a run with the model each model step runs on.
    Never a key."""
    return engine.about()
