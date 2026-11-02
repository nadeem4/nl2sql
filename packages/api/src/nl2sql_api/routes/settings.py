"""The `settings` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", row A4a): the server's LLM, the
providers, API keys and the event catalog. Thin over the facade; a refusal is the facade's own error or a 4xx here."""
from dataclasses import asdict
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from nl2sql import ApiKey, ConfigError, EventInfo, NL2SQL, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused
from nl2sql_api.routes.health import forget_ready

router = APIRouter(tags=["settings"])

Engine = Annotated[NL2SQL, Depends(get_engine)]
Admin = Annotated[UserContext, Depends(identity.admin)]

LLM_SECTION_EXAMPLE = {
    "provider": "openai",
    "model": "gpt-5.4",
    "api_key": "${env:OPENAI_API_KEY}",
    "agents": {"ast_planner": {"model": "gpt-5.5"}},
}


class ApiKeyRequest(BaseModel):
    """``POST /api-keys``: the user the key's requests run as, with their roles."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    roles: List[str] = []
    tenant_id: Optional[str] = None
    label: str = ""


class ApiKeyCreated(BaseModel):
    """The new key and its `token`, shown once: only a hash of it is stored."""

    key: ApiKey
    token: str


@router.get("/llm", response_model=Dict[str, Any], summary="List the agents' models",
            dependencies=[Depends(identity.caller)], responses=identity.AUTH_RESPONSES)
def list_llms(engine: Engine):
    """Each agent (`default` first) with the provider, model and temperature it runs on, and
    `inherited` when it sets nothing of its own, as `{"llms": ...}`. Never a key."""
    return {"llms": engine.llm.list()}


@router.get("/llm/providers", response_model=List[Dict[str, Any]], summary="List the LLM providers",
            dependencies=[Depends(identity.caller)], responses=identity.AUTH_RESPONSES)
def llm_providers(engine: Engine):
    """Every provider the engine can name (`engine.llm.providers()`): its `name`, `key_variable`, whether the key
    is present (`key_present`), the verified `models` and the `key_shape` a key must match, for a form to check one
    before sending it. Never a key."""
    return engine.llm.providers()


@router.put("/settings/llm", response_model=Dict[str, Any], summary="Replace the LLM",
            responses={**identity.ADMIN_RESPONSES,
                       422: {**ERROR_RESPONSE, "description": "The section is not valid: `error.message` is the "
                                                              "config error."}})
def replace_llm(
    request: Request,
    engine: Engine,
    section: Annotated[Dict[str, Any], Body(openapi_examples={"openai": {"value": LLM_SECTION_EXAMPLE}})],
    user: Admin,
):
    """Replace the engine's whole `llm` section with the body: the section as JSON, the same shape as
    `llm.yaml`. It is checked exactly as the file is, and every agent's key must resolve; a bad section
    is a 422 whose `error.message` is the config error, and changes nothing. Runs in flight finish on the old
    LLM. The change is logged and audited, and not written to any file: a restart reads `llm.yaml`
    again. Returns who changed it (`by`) and each agent whose provider or model changed, old and new.
    Admin only.
    """
    try:
        changed = asdict(engine.llm.replace(section, by=user, via="api"))
        forget_ready(request.app)  # a fixed or broken key shows in /ready at once
        return changed
    except ConfigError as e:
        # A ConfigError is written for the operator: it names keys and rules, never a secret value.
        raise RequestRefused(422, "INVALID_CONFIG", str(e), fix="Fix the llm section and send it again.") from None


@router.get("/api-keys", response_model=List[ApiKey], summary="List API keys", responses=identity.ADMIN_RESPONSES)
def list_api_keys(engine: Engine, user: Admin):
    """Every key, newest first, revoked ones included: its `id`, `label`, user, tenant, roles, who made it and
    when. Never a token or a hash. Admin only."""
    return engine.api_keys.list(by=user)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201, summary="Create an API key",
             responses={**identity.ADMIN_RESPONSES,
                        422: {**ERROR_RESPONSE, "description": "The user is empty or a role is not one of "
                                                               "`policies.roles`."}})
def create_api_key(body: ApiKeyRequest, engine: Engine, user: Admin):
    """A key whose requests run as `user_id` with `roles` (and `tenant_id`). The `token` is in this response only:
    keep it, the server stores a hash. Send it as `Authorization: Bearer <token>`. Admin only."""
    try:
        key, token = engine.api_keys.create(user_id=body.user_id, roles=body.roles, by=user,
                                            tenant_id=body.tenant_id, label=body.label)
    except ValueError:
        # A built-in exception's text is never echoed (B200): the two causes are named here.
        raise RequestRefused(422, "INVALID_REQUEST", "The user is empty or a role is not one of policies.roles.",
                             fix="Fix the user or the roles and send it again.") from None
    return ApiKeyCreated(key=key, token=token)


@router.delete("/api-keys/{key_id}", status_code=204, summary="Revoke an API key",
               responses={**identity.ADMIN_RESPONSES, 404: {**ERROR_RESPONSE, "description": "No such key."}})
def revoke_api_key(key_id: str, engine: Engine, user: Admin):
    """The key stops verifying at once. Revoking a revoked key changes nothing. Admin only."""
    try:
        engine.api_keys.revoke(key_id, by=user)
    except ValueError:
        raise RequestRefused(404, "NOT_FOUND", "No API key has that id.") from None
    return Response(status_code=204)


@router.get("/events/catalog", response_model=List[EventInfo], summary="List the engine's events",
            dependencies=[Depends(identity.caller)], responses=identity.AUTH_RESPONSES)
def events_catalog(engine: Engine):
    """Every event the engine publishes: its name, description, whether a run has it, whether a caller's stream
    carries it and whether the audit observer writes it, with its data's JSON Schema."""
    return engine.events.catalog()
