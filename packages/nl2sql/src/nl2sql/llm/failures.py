"""Why a call to a model provider failed, in words a caller can show.

A provider SDK raises something like ``Error code: 401 - {'error': {'message':
'Incorrect API key provided: sk-...0000', ...}}``: a Python repr with the key's
tail in it. A node that put ``str(exc)`` into its error showed exactly that to
every caller. :func:`classify_provider_error` turns it into one of a few stable
``ErrorCode`` values, a sentence naming the provider, and the provider's own
words (anything key-like cut out) as ``provider_response``. Every LLM node reports a
provider failure through :func:`provider_error`, so the SDK, the REST API and
the playground all get the same entry.

The SDKs are read by shape, not imported: the anthropic SDK is an optional extra,
and an exception from another SDK is not a provider's answer.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, NamedTuple, Optional
from urllib.parse import urlsplit

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError

from .providers import PROVIDER_LABELS, UPSTREAMS
from .registry import PROVIDER_PRESETS

__all__ = ["ProviderFailure", "classify_provider_error", "provider_error", "redact_keys"]

# Who to name when the endpoint matches no preset (a custom base_url).
UNKNOWN_PROVIDER = "The model provider"

_SDKS = {"openai", "anthropic"}
REDACTED = "[redacted key]"

# A bearer token; a prefixed key, whole or masked (sk-proj-****0000, sk-...0000);
# and any long unbroken token, which is how a raw key with no prefix looks.
_KEY_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[^\s'\",;]+"),
    re.compile(r"\b(?:sk|pk|rk)[-_](?:[^\s'\",;)]*[^\s'\",;).])?"),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
)


class ProviderFailure(NamedTuple):
    """A provider's refusal, classified.

    Attributes:
        code: One of the ``PROVIDER_*`` error codes.
        provider: The provider as a person names it, or "The model provider".
        message: One sentence naming the provider and what happened.
        provider_response: The provider's own words, with anything key-like redacted.
    """

    code: ErrorCode
    provider: str
    message: str
    provider_response: str


def redact_keys(text: str) -> str:
    """``text`` with every key-like string replaced by ``[redacted key]``."""
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` and what it was raised from, nearest first."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _sdk_of(exc: BaseException) -> Optional[str]:
    root = type(exc).__module__.split(".")[0]
    return root if root in _SDKS else None


def _provider_of(exc: BaseException, sdk: str) -> str:
    """The provider the failed call went to: Anthropic's SDK, or the endpoint it reached."""
    if sdk == "anthropic":
        return PROVIDER_LABELS["anthropic"]
    url = getattr(getattr(exc, "request", None), "url", None)
    netloc = urlsplit(str(url)).netloc if url is not None else ""
    for name, preset in PROVIDER_PRESETS.items():
        known = preset.base_url or UPSTREAMS.get(name)
        if known and urlsplit(known).netloc == netloc:
            return PROVIDER_LABELS.get(name, name)
    return UNKNOWN_PROVIDER


def _error_body(exc: BaseException) -> Any:
    """The provider's error object: OpenAI's SDK unwraps ``{"error": ...}``, Anthropic's does not."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return body


def _reason(exc: BaseException) -> str:
    """The provider's machine-readable reason (``invalid_api_key``, ``insufficient_quota``), if any."""
    body = _error_body(exc)
    if isinstance(body, dict):
        return str(body.get("code") or body.get("type") or "")
    return str(getattr(exc, "code", None) or "")


def _provider_response(exc: BaseException, status: Optional[int]) -> str:
    body = _error_body(exc)
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        words = body["message"]
    elif isinstance(body, str) and body:
        words = body
    else:
        words = getattr(exc, "message", None) or str(exc)
    prefix = f"HTTP {status}" if status else ""
    reason = _reason(exc)
    if reason:
        prefix = f"{prefix} ({reason})" if prefix else reason
    return redact_keys(f"{prefix}: {words}" if prefix else words)


def _by_status(status: int, reason: str, p: str):
    if "model_not_found" in reason or status == 404:
        return ErrorCode.PROVIDER_MODEL_UNAVAILABLE, f"{p} does not offer the configured model to this API key."
    if status == 401:
        return ErrorCode.PROVIDER_AUTH_FAILED, f"{p} rejected the API key."
    if status == 403:
        return ErrorCode.PROVIDER_AUTH_FAILED, f"{p} refused this API key permission for the request."
    if status == 429 and "insufficient_quota" in reason:
        return ErrorCode.PROVIDER_QUOTA_EXCEEDED, f"{p} says this API key has no quota left."
    if status == 429:
        return ErrorCode.PROVIDER_RATE_LIMITED, f"{p} rate limited the request."
    if status == 408:
        return ErrorCode.PROVIDER_TIMEOUT, f"{p} did not answer in time."
    if status >= 500:
        return ErrorCode.PROVIDER_UNAVAILABLE, f"{p} had a server error and could not answer."
    return None


def classify_provider_error(exc: BaseException) -> Optional[ProviderFailure]:
    """What the provider did, or None when ``exc`` is not a provider's answer.

    Looks through ``exc`` and what it was raised from for an OpenAI or
    Anthropic SDK error. A 400 is left alone: it is a request the engine built
    wrongly (or a model refusing a parameter), which its node explains better.
    """
    for candidate in _chain(exc):
        sdk = _sdk_of(candidate)
        if sdk is None:
            continue
        p = _provider_of(candidate, sdk)
        names = {cls.__name__ for cls in type(candidate).__mro__}
        status = getattr(candidate, "status_code", None)
        if "APITimeoutError" in names:
            found = ErrorCode.PROVIDER_TIMEOUT, f"{p} did not answer in time."
        elif "APIConnectionError" in names:
            found = ErrorCode.PROVIDER_UNAVAILABLE, f"{p} could not be reached."
        elif isinstance(status, int):
            found = _by_status(status, _reason(candidate), p)
        else:
            found = None
        if found:
            code, message = found
            return ProviderFailure(code, p, message, _provider_response(candidate, status if isinstance(status, int) else None))
    return None


def provider_error(node: str, exc: BaseException) -> Optional[PipelineError]:
    """The ``PipelineError`` for a provider failure, or None when ``exc`` is something else.

    The message is the provider's sentence alone: no node label, no repr, no key.
    """
    failure = classify_provider_error(exc)
    if failure is None:
        return None
    return PipelineError(
        node=node,
        message=failure.message,
        severity=ErrorSeverity.ERROR,
        error_code=failure.code,
        provider=failure.provider,
        provider_response=failure.provider_response,
    )
