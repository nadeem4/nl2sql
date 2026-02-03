from fastapi import APIRouter, Request
from nl2sql_api.models.response import SuccessResponse

router = APIRouter()


@router.get("/health", response_model=SuccessResponse)
async def health_check(request: Request):
    service = request.app.state.container.health
    return service.health_check()


@router.get("/ready", response_model=SuccessResponse)
async def readiness_check(request: Request):
    service = request.app.state.container.health
    return service.readiness_check()
