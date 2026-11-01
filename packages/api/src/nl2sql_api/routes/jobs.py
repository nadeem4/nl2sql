"""The `jobs` namespace of `/api/v1`: a background job's state, progress and cancellation. The routes that start a job
(`POST /jobs/<kind>`) are added to this file by the rows that own each kind (docs/architecture/wave_d.md, "Jobs")."""
from typing import Annotated, List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from nl2sql import NL2SQL, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine, get_jobs
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.jobs import DashboardJob, DashboardJobResult, Job, JobContext, Jobs
from nl2sql_api.sse import SSE_HEADERS

router = APIRouter(tags=["jobs"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Queue = Annotated[Jobs, Depends(get_jobs)]
Engine = Annotated[NL2SQL, Depends(get_engine)]
_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such job for this caller."}, **identity.AUTH_RESPONSES}


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
