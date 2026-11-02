"""The `jobs` namespace of `/api/v1`: a background job's state, progress and cancellation. The routes that start a job
(`POST /jobs/<kind>`) are added to this file by the rows that own each kind (docs/architecture/wave_d.md, "Jobs")."""
import logging
from typing import Annotated, List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from nl2sql import EvalReport, NL2SQL, ProfileReport, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine, get_jobs
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.jobs import (DashboardJob, DashboardJobResult, DemoJob, EvalJob, IndexJob, IndexResult, Job, JobContext,
                             Jobs, ProfileJob)
from nl2sql_api.sse import SSE_HEADERS

logger = logging.getLogger("nl2sql_api")

router = APIRouter(tags=["jobs"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Queue = Annotated[Jobs, Depends(get_jobs)]
Engine = Annotated[NL2SQL, Depends(get_engine)]
_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such job for this caller."}, **identity.AUTH_RESPONSES}


def _count(datasource_ids, engine: NL2SQL) -> int:
    """How many datasources a job goes through: the named ones (a repeated id is one) or every configured one."""
    return len(dict.fromkeys(datasource_ids if datasource_ids is not None else
                             [d.id for d in engine.config.datasources]))


def _admin(user: UserContext, engine: NL2SQL) -> bool:
    return identity.is_admin(user, engine)


# One route per kind, each a `def` that submits one runner (a function of a `JobContext`) and returns the queued job
# with a 202. A route names its body model (a `*Job` in `jobs.py`), its caller (`Caller`, or `identity.admin` for an
# admin kind) and the facade call the runner makes, reporting each item through `ctx.progress(step, done, total)` and
# passing `ctx.token` as the call's `cancellation_token`. `jobs.submit` is the one-job-per-caller check (a 409).
_STARTED = {202: {"description": "Queued: follow it with `GET /jobs/{id}` or `GET /jobs/{id}/events`."},
            409: {**ERROR_RESPONSE, "description": "The caller already has a job queued or running."},
            **identity.AUTH_RESPONSES}


@router.post("/jobs/dashboard", response_model=Job, status_code=202, summary="Generate a starter dashboard",
             responses=_STARTED)
def start_dashboard_job(body: DashboardJob, user: Caller, jobs: Queue, engine: Engine):
    """Builds the no-model starter dashboard for `datasource_id` as the caller and saves it in their workspace. The
    job's `result` is the new `Dashboard` and its first `DashboardRun`; `progress` is one step per chart."""

    def run(ctx: JobContext) -> DashboardJobResult:
        dashboard, first = engine.dashboards.generate(
            body.datasource_id, user_context=user, title=body.title, workspace_id=body.workspace_id,
            max_tiles=body.max_tiles, cancellation_token=ctx.token,
            on_tile=lambda tile: ctx.progress(f"tile {tile.done} of {tile.total}", tile.done, tile.total))
        return DashboardJobResult(dashboard=dashboard, run=first)

    return jobs.submit("dashboard", body, user, run)


_ADMIN_STARTED = {**_STARTED, **identity.ADMIN_RESPONSES}
DEMO_DATASOURCES = 3  # the sample databases `engine.add_demo()` adds


@router.post("/jobs/index", response_model=Job, status_code=202, summary="Build the index",
             responses=_ADMIN_STARTED)
def start_index_job(body: IndexJob, user: Annotated[UserContext, Depends(identity.admin)], jobs: Queue,
                    engine: Engine):
    """Indexes `datasource_ids` (every configured datasource when left out), one at a time, beside the live entries
    and switching each over when it is written. `progress` is one step per datasource (`"<id>"`, or `"<id>
    (failed)"`); the `result` is an `IndexResult`, where `failed` names the datasources that kept their previous
    index (the reason is in the server log). Admin only."""

    def run(ctx: JobContext) -> IndexResult:
        total = _count(body.datasource_ids, engine)
        finished = []

        def on_datasource(event) -> None:
            if event.state == "started":
                return
            finished.append(event.datasource_id)
            failed = event.state == "failed"
            step = f"{event.datasource_id} (failed)" if failed else event.datasource_id
            ctx.progress(step, len(finished), total)

        built = engine.index.build(body.datasource_ids, on_datasource=on_datasource, cancellation_token=ctx.token)
        for error in built.errors:
            logger.error("Indexing %s failed: %s", error["datasource_id"], error["error"])
        return IndexResult(ok=built.ok, stats=built.stats, empty=built.empty,
                           failed=[error["datasource_id"] for error in built.errors])

    return jobs.submit("index", body, user, run)


@router.post("/jobs/profile", response_model=Job, status_code=202, summary="Profile the datasources",
             responses=_ADMIN_STARTED)
def start_profile_job(body: ProfileJob, user: Annotated[UserContext, Depends(identity.admin)], jobs: Queue,
                      engine: Engine):
    """Profiles `datasource_ids` (every one when left out) and files what it finds as proposals: joins, roles and,
    with `draft`, model-written descriptions, always on the server's key (a key typed in a browser never pays for
    drafts) and capped by `max_cost_usd`. `progress` is one step per datasource; the `result` is a `ProfileReport`
    each. Admin only."""

    def run(ctx: JobContext) -> List[ProfileReport]:
        total = _count(body.datasource_ids, engine)
        done = []

        def on_datasource(report: ProfileReport) -> None:
            done.append(report.datasource_id)
            ctx.progress(report.datasource_id, len(done), total)

        return engine.knowledge.profile(body.datasource_ids, user_context=user, draft=body.draft,
                                        max_cost_usd=body.max_cost_usd, on_datasource=on_datasource,
                                        cancellation_token=ctx.token)

    return jobs.submit("profile", body, user, run)


def _suites(requested, engine: NL2SQL):
    """The configured suites a request names, by file or by name (every one when it names none): never a path the
    caller wrote, so an eval cannot read an arbitrary file. A name that is not in `evals.suites` is a 422."""
    configured = engine.config.evals.suites
    chosen = []
    if requested is None:
        chosen = list(configured)
    for name in requested or []:
        match = next((path for path in configured if name in (path.as_posix(), path.stem)), None)
        if match is None:
            raise RequestRefused(422, "INVALID_REQUEST", f"'{name}' is not one of the configured eval suites.",
                                 fix="Name a suite listed in evals.suites (GET /evals/suites), by its file or its name.")
        chosen.append(match)
    if not chosen:
        raise RequestRefused(422, "INVALID_REQUEST", "There is no eval suite to run.",
                             fix="Configure evals.suites, or name a configured suite.")
    return chosen


@router.post("/jobs/eval", response_model=Job, status_code=202, summary="Run the evals", responses=_ADMIN_STARTED)
def start_eval_job(body: EvalJob, user: Annotated[UserContext, Depends(identity.admin)], jobs: Queue, engine: Engine):
    """Runs `suites` (every configured one when left out; each by its file or name in `evals.suites`) in `mode`, on
    the server's model, never a caller's: `full` needs a cost cap, `max_cost_usd` or `evals.max_cost_usd`; `plans`
    and `retrieval` call no model. `cases` limits it to those ids or tags. No configured suite, or `suites: []`, is a 422. `progress` is one step per case
    (`"case <id>"`); the `result` is the `EvalReport`. Admin only, in every mode: a report holds every role's
    cases, plans and SQL."""
    suites = _suites(body.suites, engine)
    if body.mode == "full" and body.max_cost_usd is None and engine.config.evals.max_cost_usd is None:
        raise RequestRefused(422, "INVALID_REQUEST", "A run in mode full needs a cost cap: max_cost_usd.",
                             fix="Send max_cost_usd, or set max_cost_usd in evals.yaml.")

    def run(ctx: JobContext) -> EvalReport:
        done = []

        def on_case(config_name: str, case, dollars: float) -> None:
            done.append(case["id"])
            ctx.progress(f"case {case['id']}", len(done), None)

        return engine.evals.run(suites, mode=body.mode, cases=body.cases, max_cost_usd=body.max_cost_usd,
                                on_case=on_case, cancellation_token=ctx.token)

    return jobs.submit("eval", body, user, run)


@router.post("/jobs/demo", response_model=Job, status_code=202, summary="Try the demo", responses=_ADMIN_STARTED)
def start_demo_job(body: DemoJob, user: Annotated[UserContext, Depends(identity.admin)], jobs: Queue, engine: Engine):
    """Adds the three sample databases, their verified queries and the demo glossary to the project, then indexes
    them (`engine.add_demo()`), so a project with no data of its own has something to ask. It runs as the caller
    and needs an admin role. A second call adds nothing and indexes again. `progress` is one step per datasource;
    the `result` is an `IndexResult`. A project file that changed before the job started fails it with
    `FILE_CHANGED` and nothing is added; one that changes while it runs fails it the same way, and files written
    earlier in the run stay written. Reload the engine and run it again: a second call adds only what is missing.
    Admin only."""

    def run(ctx: JobContext) -> IndexResult:
        finished = []

        def on_datasource(event) -> None:
            if event.state == "started":
                return
            finished.append(event.datasource_id)
            failed = event.state == "failed"
            step = f"{event.datasource_id} (failed)" if failed else event.datasource_id
            ctx.progress(step, len(finished), DEMO_DATASOURCES)

        built = engine.add_demo(user_context=user, on_datasource=on_datasource)
        for error in built.errors:
            logger.error("Indexing %s failed: %s", error["datasource_id"], error["error"])
        return IndexResult(ok=built.ok, stats=built.stats, empty=built.empty,
                           failed=[error["datasource_id"] for error in built.errors])

    return jobs.submit("demo", body, user, run)


@router.get("/jobs", response_model=List[Job], summary="List jobs", responses=identity.AUTH_RESPONSES)
def list_jobs(user: Caller, jobs: Queue, engine: Engine):
    """The caller's jobs, newest first (the last 100 the server kept). An admin sees every caller's, without their
    `result`: that is the owner's alone."""
    return jobs.list(user, admin=_admin(user, engine))


@router.get("/jobs/{job_id}", response_model=Job, summary="Read a job", responses=_NOT_FOUND)
def get_job(job_id: str, user: Caller, jobs: Queue, engine: Engine):
    """One job: its `status`, `progress` so far, and `result` or `error` once it finished. Another user's job is
    a 404, except to an admin, who sees its status and progress but never its `result`."""
    return jobs.get(job_id, user, admin=_admin(user, engine))


@router.delete("/jobs/{job_id}", response_model=Job, summary="Cancel a job", responses=_NOT_FOUND)
def cancel_job(job_id: str, user: Caller, jobs: Queue, engine: Engine):
    """A queued job is removed; a running one has its token cancelled and stops between items (datasources,
    cases, tiles). Returns the job as it is now; a finished job is returned unchanged."""
    return jobs.cancel(job_id, user, admin=_admin(user, engine))


@router.get(
    "/jobs/{job_id}/events",
    summary="Follow a job",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}},
                     "description": "Server-Sent Events: one `job.progress` (a `JobProgress`) for every step so far, "
                                    "then for each new one, then `job.finished` (the `Job`) and the stream ends. "
                                    "A `: keep-alive` comment every 15 seconds."},
               **_NOT_FOUND},
)
def job_events(job_id: str, user: Caller, jobs: Queue, engine: Engine):
    """The job's progress as Server-Sent Events. These are the API's events, not the engine's."""
    jobs.get(job_id, user, admin=_admin(user, engine))  # a 404 before the stream starts
    return StreamingResponse(jobs.stream(job_id, user), media_type="text/event-stream", headers=SSE_HEADERS)
