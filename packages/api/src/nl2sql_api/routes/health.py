from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends

from nl2sql import NL2SQL
from nl2sql_api.dependencies import get_engine, get_health_service
from nl2sql_api.models.response import SuccessResponse
from nl2sql_api.services import HealthService

router = APIRouter(tags=["health"])

HealthSvc = Annotated[HealthService, Depends(get_health_service)]


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "0.0.0"


@router.get("/health", response_model=Dict[str, Any], summary="Liveness check")
def health_check():
    """Liveness, from process state only: `{"status": "ok", "version": {"engine", "api"}}`. Checks nothing else."""
    return {"status": "ok", "version": {"engine": _version("nl2sql-engine"), "api": _version("nl2sql-api")}}


@router.get("/ready", response_model=SuccessResponse, summary="Readiness check")
async def readiness_check(
    service: HealthSvc,
):
    """Returns `success: true`. It does not yet check datasources, the LLM or the index."""
    return service.readiness_check()


@router.get("/meta", response_model=Dict[str, Any], summary="What this server is")
def meta(engine: NL2SQL = Depends(get_engine)):
    """Without a caller: the `mode` (`demo`, `your_data` or `hosted`), the identity source (`auth`) and
    `request_llm`, which is what a page needs to decide whether to ask for a sign-in or a key."""
    config = engine.config
    return {"mode": config.mode, "auth": config.api.auth, "request_llm": config.api.request_llm}


@router.get("/about", response_model=Dict[str, Any], summary="The pipeline and its models")
def about(engine: NL2SQL = Depends(get_engine)):
    """`engine.about()`: the engine's version, and every step of a run with the model each model step runs on.
    Never a key."""
    return engine.about()
