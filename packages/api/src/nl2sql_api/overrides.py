"""A request's own keys and models, as the engine's ``LLMOverride`` (docs/architecture/wave_d.md, D210).

``X-NL2SQL-Api-Key-<provider>`` carries one provider's key; ``X-NL2SQL-Models`` carries ``{"<agent>": "<provider>:
<model>"}`` and no secret. Only where ``api.request_llm`` is on. A model is honoured only on a provider the same request
sent a key for, so a caller can never move a step onto the operator's account. A key is never logged or echoed.
"""
import json
from typing import Mapping, Optional

from fastapi import Depends, Request

from nl2sql import NL2SQL, LLMOverride, MissingProviderKey
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import RequestRefused

KEY_HEADER_PREFIX = "x-nl2sql-api-key-"
MODELS_HEADER = "X-NL2SQL-Models"


def _invalid(message: str) -> RequestRefused:
    return RequestRefused(422, "INVALID_REQUEST", message,
                          fix=f'Send {MODELS_HEADER} as a JSON object of agent name to "provider:model".')


def parse_override(headers: Mapping[str, str], *, request_llm: bool) -> Optional[LLMOverride]:
    """The override the headers ask for, or None when they ask for none.

    Raises:
        RequestRefused: 403 when they ask and ``request_llm`` is off; 422 when ``X-NL2SQL-Models`` is malformed.
        MissingProviderKey: a model on a provider the request sent no key for (422).
    """
    lowered = {name.lower(): value for name, value in headers.items()}
    keys = {name[len(KEY_HEADER_PREFIX):]: value.strip() for name, value in lowered.items()
            if name.startswith(KEY_HEADER_PREFIX) and name[len(KEY_HEADER_PREFIX):] and value.strip()}
    raw_models = lowered.get(MODELS_HEADER.lower())
    if not keys and raw_models is None:
        return None
    if not request_llm:
        raise RequestRefused(403, "FORBIDDEN", "This server does not take keys or models in a request (api.request_llm "
                                               "is off).",
                             fix="Remove the X-NL2SQL-Api-Key-* and X-NL2SQL-Models headers, or set api.request_llm: "
                                 "true in nl2sql.yaml.")
    models = {}
    if raw_models is not None:
        try:
            chosen = json.loads(raw_models)
        except ValueError:
            raise _invalid(f"{MODELS_HEADER} is not valid JSON.") from None
        if not isinstance(chosen, dict):
            raise _invalid(f"{MODELS_HEADER} must be a JSON object.")
        for agent, value in chosen.items():
            provider, _, model = value.partition(":") if isinstance(value, str) else ("", "", "")
            if not provider or not model:
                raise _invalid(f'{MODELS_HEADER} must give "provider:model" for each agent.')
            if provider not in keys:
                raise MissingProviderKey(agent, provider)
            models[agent] = (provider, model)
    return LLMOverride(keys=keys, models=models)


def llm_override(request: Request, engine: NL2SQL = Depends(get_engine)) -> Optional[LLMOverride]:
    """The dependency a question or turn route takes, passing the result as ``llm_override=``."""
    return parse_override(request.headers, request_llm=engine.config.api.request_llm)
