from fastapi import APIRouter, Depends
from typing import Annotated
from nl2sql_api.models.response import SuccessResponse
from nl2sql_api.dependencies import get_health_service
from nl2sql_api.services import HealthService

router = APIRouter(tags=["health"])

HealthSvc = Annotated[HealthService, Depends(get_health_service)]

@router.get("/health", response_model=SuccessResponse, summary="Liveness check")
async def health_check(
    service: HealthSvc
):
    """Returns `success: true` while the process is serving. Checks nothing else."""
    return service.health_check()


@router.get("/ready", response_model=SuccessResponse, summary="Readiness check")
async def readiness_check(
    service: HealthSvc,
):
    """Returns `success: true`. It does not yet check datasources, the LLM or the index."""
    return service.readiness_check()
