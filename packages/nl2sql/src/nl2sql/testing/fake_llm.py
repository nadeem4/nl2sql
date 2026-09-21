"""OpenAI-compatible chat.completions fake.

Dispatches on the structured-output name the client asks for (the function name
under ``tools`` in function_calling mode, ``response_format.json_schema.name`` in
json_schema mode, or ``plain`` for a free-text call) plus an optional substring
that must appear in the prompt text. The first matching rule wins. Used by the
end-to-end tests and by ``nl2sql demo`` replay mode.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Union

Payload = Union[Dict[str, Any], str, Callable[[str], Union[Dict[str, Any], str]]]


DEFAULT_USAGE: Dict[str, Any] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

# OpenAI's HTTP 400 for gpt-5.5 and gpt-5-mini when sent temperature=0, verbatim
# from a probe on 2026-09-20.
TEMPERATURE_REJECTED = (
    "Unsupported value: 'temperature' does not support 0.0 with this model. "
    "Only the default (1) value is supported."
)


@dataclass
class Rule:
    """``usage`` is the OpenAI ``usage`` object returned with each matching answer.

    It defaults to one prompt and one completion token. Set it to report
    realistic counts, including ``prompt_tokens_details.cached_tokens`` and
    ``completion_tokens_details.reasoning_tokens``.
    """

    name: str
    payload: Payload
    when: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


def classify_request(body: Dict[str, Any]) -> tuple:
    """Classifies a chat.completions request body.

    Returns ``(mode, name, prompt_text)`` where ``mode`` is ``"tools"``,
    ``"json_schema"`` or ``"plain"``, ``name`` is the structured-output name the
    client asked for (``"plain"`` for a free-text call), and ``prompt_text`` is
    every message's content joined by newlines.

    Shared with ``nl2sql.llm.replay.RecordingProxy`` so a recording is keyed
    exactly the way the replay server later dispatches on it.
    """
    tools = body.get("tools") or []
    rf = body.get("response_format") or {}
    if tools:
        mode, name = "tools", tools[0]["function"]["name"]
    elif rf.get("type") == "json_schema":
        mode, name = "json_schema", rf["json_schema"]["name"]
    else:
        mode, name = "plain", "plain"
    text = "\n".join(str(m.get("content") or "") for m in body.get("messages", []))
    return mode, name, text


def completion(body: Dict[str, Any], mode: str, name: str, payload: Any,
               usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """An OpenAI ``chat.completion`` answering ``body`` with ``payload``.

    ``mode`` and ``name`` come from :func:`classify_request`: a ``tools`` call is
    answered with a tool call named ``name`` whose arguments are ``payload``
    (a JSON-serialisable value, or an already-serialised string), a
    ``json_schema`` call with ``payload`` as JSON content, a plain call with
    ``payload`` as text. Shared with ``nl2sql.tracing.replay``.
    """
    if mode == "tools":
        arguments = payload if isinstance(payload, str) else json.dumps(payload)
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}]}
        finish = "tool_calls"
    elif mode == "json_schema":
        content = payload if isinstance(payload, str) else json.dumps(payload)
        msg, finish = {"role": "assistant", "content": content}, "stop"
    else:
        msg, finish = {"role": "assistant", "content": str(payload)}, "stop"
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": usage or DEFAULT_USAGE}


@dataclass
class FakeLLMServer:
    """``reject_temperature`` answers any request carrying ``temperature`` with
    OpenAI's 400 for models that only accept the default, as gpt-5.5 does.

    Each entry in ``calls`` keeps the request ``body``, and the ``authorization``
    header of a matched or unmatched call, so tests can check what was actually
    sent and with which key.
    """

    rules: List[Rule]
    host: str = "127.0.0.1"
    port: int = 0
    reject_temperature: bool = False
    calls: List[Dict[str, Any]] = field(default_factory=list)
    _server: Optional[HTTPServer] = None
    _thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "call start() first"
        return f"http://{self.host}:{self._server.server_address[1]}/v1"

    def start(self) -> "FakeLLMServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _send(self, status: int, payload: Dict[str, Any]) -> None:
                out = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mode, name, text = classify_request(body)
                if outer.reject_temperature and "temperature" in body:
                    outer.calls.append({"name": name, "mode": mode, "matched": False, "body": body})
                    self._send(400, {"error": {"message": TEMPERATURE_REJECTED,
                                               "type": "invalid_request_error",
                                               "param": "temperature", "code": "unsupported_value"}})
                    return
                rule = next(
                    (r for r in outer.rules if r.name == name and (r.when is None or r.when in text)),
                    None,
                )
                outer.calls.append({"name": name, "mode": mode, "matched": rule is not None, "body": body,
                                    "authorization": self.headers.get("Authorization")})
                if rule is None:
                    self._send(400, {"error": {"message": f"fake llm: no rule for {name}"}})
                    return
                payload = rule.payload(text) if callable(rule.payload) else rule.payload
                self._send(200, completion(body, mode, name, payload, rule.usage))

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
