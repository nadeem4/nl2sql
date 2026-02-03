from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

from nl2sql_api.models.llm import LLMRequest, LLMResponse

router = APIRouter()


@router.post("/llm", response_model=LLMResponse)
async def configure_llm(request: Request, payload: LLMRequest):
    try:
        service = request.app.state.container.llm
        return service.configure_llm(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm", response_model=Dict[str, Any])
async def list_llms(request: Request):
    try:
        service = request.app.state.container.llm
        return {"llms": service.list_llms()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/llm/{llm_name}", response_model=Dict[str, Any])
async def get_llm(request: Request, llm_name: str):
    try:
        service = request.app.state.container.llm
        return service.get_llm(llm_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
