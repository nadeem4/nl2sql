"""A provider's refusal reaches the caller as a stable code and a sentence.

The model provider's own exception reads like ``Error code: 401 - {'error':
{'message': 'Incorrect API key provided: sk-...0000', ...}}``: a Python repr,
with the key's tail in it. ``nl2sql.llm.failures`` turns that into an
``ErrorCode`` a caller can branch on, a short message naming the provider, and
the provider's own words (with any key cut out) in ``detail``. No network: the
exceptions are the SDK's own, built from a stub response.
"""
import re

import openai
import pytest

try:  # the transport openai builds its exceptions on
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older openai releases
    import httpx

from nl2sql.common.errors import ErrorCode
from nl2sql.llm.failures import classify_provider_error, provider_error, redact_keys

# Built at run time so no scanner mistakes a fixture for a leaked key.
RAW_KEY = "-".join(["sk", "proj", "failures" + "x" * 40 + "0000"])
OPENAI_MASKED = "sk-proj-" + "*" * 40 + "0000"
ELLIPSIS_MASKED = "sk-…0000"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _status_error(status, body, url=OPENAI_URL):
    """The exception openai raises for ``status``, as its client builds it."""
    request = httpx.Request("POST", url)
    response = httpx.Response(status, request=request, json={"error": body})
    client = openai.OpenAI(api_key="unused-in-this-test")
    return client._make_status_error(f"Error code: {status} - {{'error': {body!r}}}",
                                     body={"error": body}, response=response)


def _openai_body(message, code=None, type_="invalid_request_error"):
    return {"message": message, "type": type_, "param": None, "code": code}


AUTH = _openai_body(f"Incorrect API key provided: {OPENAI_MASKED}. You can find your API key at "
                    "https://platform.openai.com/account/api-keys.", code="invalid_api_key")

CASES = [
    ("auth_401", lambda: _status_error(401, AUTH), ErrorCode.PROVIDER_AUTH_FAILED),
    ("auth_403", lambda: _status_error(403, _openai_body("You are not allowed to sample from this model")),
     ErrorCode.PROVIDER_AUTH_FAILED),
    ("model_404", lambda: _status_error(404, _openai_body(
        "The model `gpt-9` does not exist or you do not have access to it.", code="model_not_found")),
     ErrorCode.PROVIDER_MODEL_UNAVAILABLE),
    ("rate_429", lambda: _status_error(429, _openai_body(
        "Rate limit reached for gpt-5.4 on tokens per min (TPM).", code="rate_limit_exceeded",
        type_="tokens")), ErrorCode.PROVIDER_RATE_LIMITED),
    ("quota_429", lambda: _status_error(429, _openai_body(
        "You exceeded your current quota, please check your plan and billing details.",
        code="insufficient_quota", type_="insufficient_quota")), ErrorCode.PROVIDER_QUOTA_EXCEEDED),
    ("server_500", lambda: _status_error(500, _openai_body("The server had an error.", type_="server_error")),
     ErrorCode.PROVIDER_UNAVAILABLE),
    ("server_503", lambda: _status_error(503, _openai_body("Service unavailable.", type_="server_error")),
     ErrorCode.PROVIDER_UNAVAILABLE),
    ("timeout", lambda: openai.APITimeoutError(request=httpx.Request("POST", OPENAI_URL)),
     ErrorCode.PROVIDER_TIMEOUT),
    ("connection", lambda: openai.APIConnectionError(request=httpx.Request("POST", OPENAI_URL)),
     ErrorCode.PROVIDER_UNAVAILABLE),
]


@pytest.mark.parametrize("make, code", [(m, c) for _, m, c in CASES], ids=[i for i, _, _ in CASES])
def test_each_provider_failure_gets_its_code_and_a_sentence_naming_the_provider(make, code):
    failure = classify_provider_error(make())

    assert failure is not None
    assert failure.code == code
    assert failure.provider == "OpenAI"
    assert "OpenAI" in failure.message
    # A sentence, not a repr or a status line.
    assert "{" not in failure.message and "Error code" not in failure.message
    assert "sk-" not in failure.message


def test_the_provider_is_named_from_the_endpoint_the_call_went_to():
    exc = _status_error(401, AUTH, url="https://openrouter.ai/api/v1/chat/completions")

    assert classify_provider_error(exc).provider == "OpenRouter"


def test_an_endpoint_no_preset_names_is_the_model_provider():
    exc = _status_error(401, AUTH, url="http://127.0.0.1:9999/v1/chat/completions")

    failure = classify_provider_error(exc)

    assert failure.provider == "The model provider"
    assert failure.message.startswith("The model provider")


def test_an_anthropic_exception_is_classified_without_the_anthropic_sdk():
    # The anthropic extra is optional, so the classifier reads its exceptions by
    # shape. These stand-ins have the SDK's module, names and attributes.
    class APIStatusError(Exception):
        def __init__(self, status, body):
            super().__init__(f"Error code: {status} - {body!r}")
            self.status_code = status
            self.body = body

    class AuthenticationError(APIStatusError):
        pass

    for cls in (APIStatusError, AuthenticationError):
        cls.__module__ = "anthropic._exceptions"

    exc = AuthenticationError(401, {"type": "error", "error": {"type": "authentication_error",
                                                               "message": "invalid x-api-key"}})

    failure = classify_provider_error(exc)

    assert failure.code == ErrorCode.PROVIDER_AUTH_FAILED
    assert failure.provider == "Anthropic"
    assert "invalid x-api-key" in failure.detail


def test_a_wrapped_provider_exception_is_found_through_its_cause():
    inner = _status_error(429, _openai_body("Rate limit reached.", code="rate_limit_exceeded"))
    try:
        try:
            raise inner
        except openai.RateLimitError as exc:
            raise RuntimeError("chain failed") from exc
    except RuntimeError as outer:
        failure = classify_provider_error(outer)

    assert failure.code == ErrorCode.PROVIDER_RATE_LIMITED


@pytest.mark.parametrize("exc", [ValueError("bad plan"), TimeoutError("pipeline"),
                                 _status_error(400, _openai_body("Invalid schema"))])
def test_anything_else_is_left_to_the_node_that_caught_it(exc):
    assert classify_provider_error(exc) is None


def test_detail_keeps_the_providers_words_without_the_key_or_a_repr():
    failure = classify_provider_error(_status_error(401, AUTH))

    assert "Incorrect API key provided" in failure.detail
    assert "401" in failure.detail
    assert "invalid_api_key" in failure.detail
    assert "{'" not in failure.detail
    assert OPENAI_MASKED not in failure.detail
    assert "0000" not in failure.detail


@pytest.mark.parametrize("key", [RAW_KEY, OPENAI_MASKED, ELLIPSIS_MASKED,
                                 "-".join(["sk", "ant", "api03", "c" * 40]),
                                 "Bearer " + "a1b2c3d4" * 5])
def test_no_key_like_string_survives_redaction(key):
    redacted = redact_keys(f"Incorrect API key provided: {key}. Try again.")

    assert key not in redacted
    assert not re.search(r"sk-\S", redacted)
    assert "Incorrect API key provided" in redacted and "Try again." in redacted


def test_no_key_survives_in_message_or_detail_even_when_the_provider_echoes_it_whole():
    body = _openai_body(f"Incorrect API key provided: {RAW_KEY}.", code="invalid_api_key")

    error = provider_error("datasource_resolver", _status_error(401, body))

    for text in (error.message, error.detail):
        assert RAW_KEY not in text and RAW_KEY[-4:] not in text


def test_provider_error_is_a_fatal_pipeline_error_that_names_no_node():
    error = provider_error("datasource_resolver", _status_error(401, AUTH))

    assert error.error_code == ErrorCode.PROVIDER_AUTH_FAILED
    assert error.provider == "OpenAI"
    assert error.node == "datasource_resolver"
    assert "datasource" not in error.message.lower() and "resolution" not in error.message.lower()
    # Retrying through the refiner would make the same call with the same key.
    assert error.is_retryable is False


def test_provider_error_is_none_for_a_failure_that_is_not_the_providers():
    assert provider_error("ast_planner", ValueError("bad plan")) is None
