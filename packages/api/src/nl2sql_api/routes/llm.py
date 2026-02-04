from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api.models.llm import LLMRequest, LLMResponse
from nl2sql_api.dependencies import get_llm_service
from nl2sql_api.services import LLMService

router = APIRouter()

LLMSvc = Annotated[LLMService, Depends(get_llm_service)]

@router.post("/llm", response_model=LLMResponse)
async def configure_llm(
    payload: LLMRequest,
    service: LLMSvc,
):
    try:
        return service.configure_llm(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm", response_model=Dict[str, Any])
async def list_llms(
    service: LLMSvc,
):
    try:
        return {"llms": service.list_llms()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm/{llm_name}", response_model=Dict[str, Any])
async def get_llm(
    llm_name: str,
    service: LLMSvc,
):
    try:
        return service.get_llm(llm_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
