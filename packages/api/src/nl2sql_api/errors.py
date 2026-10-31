"""One error envelope for every non-2xx response (docs/architecture/wave_d.md, D202).

``{"error": {"code", "message", "fix", "details", "request_id"}}``. Only the facade's own ``NL2SQLError`` is mapped to
its status; a route that refuses on its own raises :class:`RequestRefused`. Anything else is a 500 that names a request
id and never its text: the exception is logged under that id.
"""
import logging
import uuid
from http import HTTPStatus
from typing import Any, Dict, List, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nl2sql import NL2SQLError

logger = logging.getLogger("nl2sql_api")

REQUEST_ID_HEADER = "X-Request-ID"

# The codes the API itself uses; an NL2SQLError brings its own.
_CODES = {400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT",
          422: "INVALID_REQUEST", 429: "LIMIT_REACHED", 503: "NOT_READY"}


class ApiError(BaseModel):
    code: str  # an NL2SQLError's code, or NOT_FOUND, INVALID_REQUEST, UNAUTHENTICATED, FORBIDDEN, CONFLICT, ...
    message: str  # one sentence a person can act on
    fix: Optional[str] = None
    details: List[Dict[str, Any]] = []  # 422: one entry per problem, {"loc": [...], "msg": ...}; never a value
    request_id: Optional[str] = None  # 500 only: the id in the server log


class ErrorBody(BaseModel):
    error: ApiError


ERROR_RESPONSE = {"model": ErrorBody}


class RequestRefused(NL2SQLError):
    """A route's or a dependency's own refusal, with the message it wrote (never an exception's text).

    ``headers`` ride on the response (``WWW-Authenticate`` for a 401); ``details`` are 422's per-problem entries.
    """

    def __init__(self, status: int, code: str, message: str, *, fix: Optional[str] = None,
                 details: Optional[List[Dict[str, Any]]] = None, headers: Optional[Mapping[str, str]] = None):
        super().__init__(message, fix=fix, code=code, status=status)
        self.details = list(details or [])
        self.headers = dict(headers or {})


def request_id_of(request: Request) -> str:
    """The id of this request, made on the way in (or here, if the middleware did not run)."""
    state = request.scope.setdefault("state", {})
    if "request_id" not in state:
        state["request_id"] = uuid.uuid4().hex[:12]
    return state["request_id"]


def _response(request: Request, status: int, error: ApiError, headers: Optional[Mapping[str, str]] = None):
    headers = {**(headers or {}), REQUEST_ID_HEADER: request_id_of(request)}
    return JSONResponse(ErrorBody(error=error).model_dump(mode="json"), status_code=status, headers=headers)


def _internal(request: Request, exc: BaseException) -> JSONResponse:
    request_id = request_id_of(request)
    logger.error("Request %s failed: %s %s", request_id, request.method, request.url.path, exc_info=exc)
    return _response(request, 500, ApiError(
        code="INTERNAL", message=f"Something failed on the server. Request id: {request_id}",
        fix="Give the request id to whoever runs the server; the cause is in its log.", request_id=request_id))


def _nl2sql_error(request: Request, exc: NL2SQLError) -> JSONResponse:
    if exc.status >= 500 and not isinstance(exc, RequestRefused):
        return _internal(request, exc)
    error = ApiError(code=exc.code, message=exc.message, fix=exc.fix, details=getattr(exc, "details", []))
    return _response(request, exc.status, error, getattr(exc, "headers", None))


def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only where and why, never the value: a rejected field may be a password.
    details = [{"loc": list(problem["loc"]), "msg": problem["msg"]} for problem in exc.errors()]
    return _response(request, 422, ApiError(
        code="INVALID_REQUEST", message="The request does not validate.",
        fix="Fix the fields listed in details.", details=details))


def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    code = _CODES.get(exc.status_code, "INVALID_REQUEST" if exc.status_code < 500 else "INTERNAL")
    return _response(request, exc.status_code, ApiError(code=code, message=message), exc.headers)


async def _internal_handler(request: Request, exc: Exception) -> JSONResponse:
    return _internal(request, exc)


class RequestIdMiddleware:
    """Gives each request an id and sends it back as ``X-Request-ID``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = scope.setdefault("state", {}).setdefault("request_id", uuid.uuid4().hex[:12])

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode())]}
            await send(message)

        await self.app(scope, receive, send_with_id)


def install(app: FastAPI) -> None:
    """Register the handlers and the request-id middleware on ``app``."""
    app.add_exception_handler(NL2SQLError, _nl2sql_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _internal_handler)
    app.add_middleware(RequestIdMiddleware)
