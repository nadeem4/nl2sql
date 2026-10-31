from typing import Any, Annotated, Dict

from fastapi import APIRouter, Body, Depends

from nl2sql import ConfigError, UserContext
from nl2sql_api import identity
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.dependencies import get_llm_service
from nl2sql_api.services import LLMService

router = APIRouter(tags=["llm"])

LLMSvc = Annotated[LLMService, Depends(get_llm_service)]

LLM_SECTION_EXAMPLE = {
    "provider": "openai",
    "model": "gpt-5.4",
    "api_key": "${env:OPENAI_API_KEY}",
    "agents": {"ast_planner": {"model": "gpt-5.5"}},
}


@router.get("/llm", response_model=Dict[str, Any], summary="List the agents' models")
def list_llms(
    service: LLMSvc,
):
    """Each agent (`default` first) with the provider, model and temperature it runs on, and
    `inherited` when it sets nothing of its own, as `{"llms": ...}`. Never a key."""
    return {"llms": service.list_llms()}


@router.get("/llm/{agent}", response_model=Dict[str, Any], summary="Get one agent's model")
def get_llm(
    agent: str,
    service: LLMSvc,
):
    """One agent's entry, by its name in the `llm` section (`default`, `ast_planner`, ...). 404 for
    an unknown agent."""
    try:
        return service.get_llm(agent)
    except ValueError:
        raise RequestRefused(404, "NOT_FOUND", f"No agent '{agent}' in the llm section.") from None


@router.put("/settings/llm", response_model=Dict[str, Any], summary="Replace the LLM",
            responses={**identity.ADMIN_RESPONSES,
                       422: {**ERROR_RESPONSE, "description": "The section is not valid: `error.message` is the "
                                                              "config error."}})
def replace_llm(
    service: LLMSvc,
    section: Annotated[Dict[str, Any], Body(openapi_examples={"openai": {"value": LLM_SECTION_EXAMPLE}})],
    user: UserContext = Depends(identity.admin),
):
    """Replace the engine's whole `llm` section with the body: the section as JSON, the same shape as
    `llm.yaml`. It is checked exactly as the file is, and every agent's key must resolve; a bad section
    is a 422 whose `error.message` is the config error, and changes nothing. Runs in flight finish on the old
    LLM. The change is logged and audited, and not written to any file: a restart reads `llm.yaml`
    again. Returns who changed it (`by`) and each agent whose provider or model changed, old and new.
    Admin only.
    """
    try:
        return service.replace_llm(section, by=user)
    except ConfigError as e:
        # A ConfigError is written for the operator: it names keys and rules, never a secret value.
        raise RequestRefused(422, "INVALID_CONFIG", str(e), fix="Fix the llm section and send it again.") from None
