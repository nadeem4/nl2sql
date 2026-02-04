from fastapi import APIRouter, Depends
from typing import Annotated
from nl2sql_api.models.response import SuccessResponse
from nl2sql_api.dependencies import get_health_service
from nl2sql_api.services import HealthService

router = APIRouter()

HealthSvc = Annotated[HealthService, Depends(get_health_service)]

@router.get("/health", response_model=SuccessResponse)
async def health_check(
    service: HealthSvc
):
    return service.health_check()


@router.get("/ready", response_model=SuccessResponse)
async def readiness_check(
    service: HealthSvc,
):
    return service.readiness_check()
