from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api.models.llm import LLMRequest, LLMResponse
from nl2sql_api.dependencies import get_llm_service
from nl2sql_api.services import LLMService

router = APIRouter(tags=["llm"])

LLMSvc = Annotated[LLMService, Depends(get_llm_service)]

@router.post("/llm", response_model=LLMResponse, summary="Configure an LLM")
def configure_llm(
    payload: LLMRequest,
    service: LLMSvc,
):
    """Register an LLM in this process from `config` (`name` defaults to `default`).
    It is not written to `configs/llm.yaml`.
    """
    try:
        return service.configure_llm(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm", response_model=Dict[str, Any], summary="List LLMs")
def list_llms(
    service: LLMSvc,
):
    """The configured LLMs, as `{"llms": ...}`."""
    try:
        return {"llms": service.list_llms()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm/{llm_name}", response_model=Dict[str, Any], summary="Get an LLM")
def get_llm(
    llm_name: str,
    service: LLMSvc,
):
    """One configured LLM by name."""
    try:
        return service.get_llm(llm_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
