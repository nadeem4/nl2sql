"""Background jobs: in process, one worker thread, in memory (docs/architecture/wave_d.md, D206 and "Jobs").

A job is one facade call (index, profile, eval, dashboard) run by ``Jobs``' single worker, in submission order. A route
submits a runner, a function of a :class:`JobContext`, which reports progress and watches its cancellation token. The
last ``keep`` jobs are held; a restart loses them (what they produced is in the engine's stores).
"""
import asyncio
import logging
import queue
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel

from nl2sql import (CancellationToken, Dashboard, DashboardRun, EvalReport, NL2SQLError, ProfileReport,
                    RunCancelled, UserContext)
from nl2sql_api.errors import ApiError, RequestRefused
from nl2sql_api.sse import KEEP_ALIVE, frame

logger = logging.getLogger("nl2sql_api")

JobKind = Literal["index", "profile", "eval", "dashboard", "demo"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class IndexJob(BaseModel):
    datasource_ids: Optional[List[str]] = None


class ProfileJob(BaseModel):
    datasource_ids: Optional[List[str]] = None
    draft: bool = False
    max_cost_usd: Optional[float] = None


class EvalJob(BaseModel):
    suites: Optional[List[str]] = None
    mode: Literal["full", "plans", "retrieval"] = "full"
    cases: Optional[List[str]] = None
    max_cost_usd: Optional[float] = None


class DashboardJob(BaseModel):
    datasource_id: str
    title: Optional[str] = None
    workspace_id: Optional[str] = None
    max_tiles: Optional[int] = None


class IndexResult(BaseModel):
    """What an index build did. ``failed`` names the datasources that kept their previous index; the reason is in the
    server log, where a connection string cannot reach a client."""

    ok: bool
    stats: List[Dict[str, Any]] = []
    empty: List[str] = []
    failed: List[str] = []


class DashboardJobResult(BaseModel):
    """What ``engine.dashboards.generate`` returns, as a model (not the engine's ``DashboardGenerated`` event)."""

    dashboard: Dashboard
    run: DashboardRun


class JobProgress(BaseModel):
    at: datetime
    step: str  # "chinook", "case 12 of 80", "tile 3 of 8"
    done: int
    total: Optional[int] = None


class Job(BaseModel):
    id: str  # 22 url-safe characters
    kind: JobKind
    status: JobStatus
    params: Union[IndexJob, ProfileJob, EvalJob, DashboardJob]  # the request body, typed per kind
    created_by: Optional[str] = None  # the caller's user_id
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    progress: List[JobProgress] = []
    result: Union[IndexResult, List[ProfileReport], EvalReport, DashboardJobResult, None] = None  # typed per kind
    error: Optional[ApiError] = None


class JobContext:
    """What a runner gets: ``progress`` to report an item done, and the token to check between items."""

    def __init__(self, token: CancellationToken, report: Callable[[str, int, Optional[int]], None]):
        self.token = token
        self._report = report

    def progress(self, step: str, done: int, total: Optional[int] = None) -> None:
        self._report(step, done, total)


Runner = Callable[[JobContext], Any]
_FINISHED = ("succeeded", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return secrets.token_urlsafe(16)  # 22 characters


def _owner(user: UserContext) -> Tuple[Optional[str], Optional[str]]:
    return (user.tenant_id, user.user_id)


_INTERNAL = ApiError(code="INTERNAL", message="The job failed.",
                     fix="The cause is in the server log, under this job's id.")


class _Entry:
    def __init__(self, job: Job, owner: Tuple[Optional[str], Optional[str]], run: Runner):
        self.job, self.owner, self.run, self.token = job, owner, run, CancellationToken()
        self.returned = False  # the runner is done: a cancel after this is too late


class Jobs:
    """The queue, the worker and the last ``keep`` jobs. ``poll_s`` is how often an events stream looks for news."""

    def __init__(self, *, keep: int = 100, poll_s: float = 0.25, heartbeat_s: float = 15.0,
                 clock: Callable[[], datetime] = _now, new_id: Callable[[], str] = _new_id):
        self._keep, self._poll_s, self._heartbeat_s, self._clock, self._new_id = keep, poll_s, heartbeat_s, clock, new_id
        self._lock = threading.Lock()
        self._entries: Dict[str, _Entry] = {}  # in submission order
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    # ---- the routes' side

    def submit(self, kind: JobKind, params: BaseModel, user: UserContext, run: Runner) -> Job:
        """Queue ``run`` for ``user``. A caller with a job queued or running gets a 409 naming it."""
        owner = _owner(user)
        with self._lock:
            busy = next((e.job for e in self._entries.values()
                         if e.owner == owner and e.job.status in ("queued", "running")), None)
            if busy is not None:
                raise RequestRefused(409, "CONFLICT", f"You already have job {busy.id} {busy.status}.",
                                     fix="Wait for it to finish, or cancel it with DELETE /jobs/{id}.")
            job = Job(id=self._new_id(), kind=kind, status="queued", params=params, created_by=user.user_id,
                      created_at=self._clock())
            self._entries[job.id] = _Entry(job, owner, run)
            self._prune()
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name="nl2sql-jobs", daemon=True)
                self._worker.start()
        self._queue.put(job.id)
        return job.model_copy(deep=True)

    def get(self, job_id: str, user: UserContext, *, admin: bool = False) -> Job:
        with self._lock:
            return self._visible(job_id, user, admin).job.model_copy(deep=True)

    def list(self, user: UserContext, *, admin: bool = False) -> List[Job]:
        """The caller's jobs, newest first; an admin sees every caller's."""
        with self._lock:
            owner = _owner(user)
            return [e.job.model_copy(deep=True) for e in reversed(list(self._entries.values()))
                    if admin or e.owner == owner]

    def cancel(self, job_id: str, user: UserContext, *, admin: bool = False) -> Job:
        """A queued job is removed; a running one has its token cancelled and stops between items."""
        with self._lock:
            entry = self._visible(job_id, user, admin)
            if entry.job.status == "queued":
                entry.job.status, entry.job.finished_at = "cancelled", self._clock()
            elif entry.job.status == "running" and not entry.returned:
                entry.token.cancel()
            return entry.job.model_copy(deep=True)

    async def stream(self, job_id: str):
        """Every progress so far, then each new one, then ``job.finished`` with the job. An async generator over
        the in-memory job: a waiting browser holds no thread."""
        sent, idle = 0, 0.0
        while True:
            with self._lock:
                entry = self._entries.get(job_id)
                if entry is None:
                    return
                job = entry.job.model_copy(deep=True)
            for progress in job.progress[sent:]:
                yield frame("job.progress", progress.model_dump_json())
                idle = 0.0
            sent = len(job.progress)
            if job.status in _FINISHED:
                yield frame("job.finished", job.model_dump_json())
                return
            await asyncio.sleep(self._poll_s)
            idle += self._poll_s
            if idle >= self._heartbeat_s:
                yield KEEP_ALIVE
                idle = 0.0

    def close(self) -> None:
        """Stop the worker: the running job is cancelled, the queued ones are dropped."""
        with self._lock:
            for entry in self._entries.values():
                if entry.job.status in ("queued", "running"):
                    entry.token.cancel()
            worker = self._worker
        self._queue.put(None)
        if worker is not None:
            worker.join(timeout=5)

    # ---- the worker's side

    def _visible(self, job_id: str, user: UserContext, admin: bool) -> _Entry:
        entry = self._entries.get(job_id)
        if entry is None or not (admin or entry.owner == _owner(user)):
            raise RequestRefused(404, "NOT_FOUND", "No such job.")
        return entry

    def _prune(self) -> None:
        finished = [i for i, e in self._entries.items() if e.job.status in _FINISHED]
        for job_id in finished[:max(0, len(self._entries) - self._keep)]:
            del self._entries[job_id]

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                entry = self._entries.get(job_id)
                if entry is None or entry.job.status != "queued" or entry.token.is_cancelled():
                    continue  # cancelled while it waited
                entry.job.status, entry.job.started_at = "running", self._clock()
            self._run(entry)

    def _run(self, entry: _Entry) -> None:
        def report(step: str, done: int, total: Optional[int]) -> None:
            with self._lock:
                entry.job.progress.append(JobProgress(at=self._clock(), step=step, done=done, total=total))

        result, error = None, None
        try:
            result = entry.run(JobContext(entry.token, report))
        except RunCancelled:  # a cancel: the token is set, so the job ends cancelled below, with no error
            pass
        except RequestRefused as exc:
            error = ApiError(code=exc.code, message=exc.message, fix=exc.fix, details=exc.details)
        except NL2SQLError as exc:
            if exc.status >= 500:  # its text may hold a connection string: the log has it, the job does not
                logger.exception("Job %s (%s) failed", entry.job.id, entry.job.kind)
                error = _INTERNAL
            else:
                error = ApiError(code=exc.code, message=exc.message, fix=exc.fix, details=getattr(exc, "details", []))
        except Exception:
            logger.exception("Job %s (%s) failed", entry.job.id, entry.job.kind)
            error = _INTERNAL
        with self._lock:
            entry.returned = True  # from here a cancel changes nothing
            cancelled = entry.token.is_cancelled()
        finished_at = self._clock()
        with self._lock:
            job = entry.job
            job.finished_at = finished_at
            if cancelled:
                job.status = "cancelled"
            elif error is not None:
                job.status, job.error = "failed", error
            else:
                job.status, job.result = "succeeded", result
            self._prune()
