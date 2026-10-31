"""The `jobs` namespace of `/api/v1`: a background job's state, progress and cancellation. The routes that start a job
(`POST /jobs/<kind>`) are added to this file by the rows that own each kind (docs/architecture/wave_d.md, "Jobs")."""
from typing import Annotated, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from nl2sql import NL2SQL, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine, get_jobs
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.jobs import Job, Jobs
from nl2sql_api.sse import SSE_HEADERS

router = APIRouter(tags=["jobs"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Queue = Annotated[Jobs, Depends(get_jobs)]
_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such job for this caller."}, **identity.AUTH_RESPONSES}


def _admin(user: UserContext, request: Request) -> bool:
    return identity.is_admin(user, request.app.state.engine)


@router.get("/jobs", response_model=List[Job], summary="List jobs", responses=identity.AUTH_RESPONSES)
def list_jobs(user: Caller, jobs: Queue, request: Request):
    """The caller's jobs, newest first (the last 100 the server kept). An admin sees every caller's."""
    return jobs.list(user, admin=_admin(user, request))


@router.get("/jobs/{job_id}", response_model=Job, summary="Read a job", responses=_NOT_FOUND)
def get_job(job_id: str, user: Caller, jobs: Queue, request: Request):
    """One job: its `status`, `progress` so far, and `result` or `error` once it finished. Another user's job is
    a 404."""
    return jobs.get(job_id, user, admin=_admin(user, request))


@router.delete("/jobs/{job_id}", response_model=Job, summary="Cancel a job", responses=_NOT_FOUND)
def cancel_job(job_id: str, user: Caller, jobs: Queue, request: Request):
    """A queued job is removed; a running one has its token cancelled and stops between items (datasources,
    cases, tiles). Returns the job as it is now; a finished job is returned unchanged."""
    return jobs.cancel(job_id, user, admin=_admin(user, request))


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
def job_events(job_id: str, user: Caller, jobs: Queue, request: Request):
    """The job's progress as Server-Sent Events. These are the API's events, not the engine's."""
    jobs.get(job_id, user, admin=_admin(user, request))  # a 404 before the stream starts
    return StreamingResponse(jobs.stream(job_id), media_type="text/event-stream", headers=SSE_HEADERS)
