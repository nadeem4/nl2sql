from fastapi import APIRouter
from nl2sql_api.models.response import SuccessResponse

router = APIRouter()

@router.get("/health", response_model=SuccessResponse)
async def health_check():
    return SuccessResponse(success=True, message="NL2SQL API is running")

@router.get("/ready", response_model=SuccessResponse)
async def readiness_check():
    # TODO: Add actual readiness checks (database connections, etc.)
    return SuccessResponse(success=True, message="NL2SQL API is ready")