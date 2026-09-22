"""OpenAI-compatible chat.completions fake, which also answers Anthropic's Messages API.

Dispatches on the structured-output name the client asks for (the function name
under ``tools`` in function_calling mode, ``response_format.json_schema.name`` in
json_schema mode, or ``plain`` for a free-text call) plus an optional substring
that must appear in the prompt text. The first matching rule wins. Used by the
end-to-end tests and by ``nl2sql demo`` replay mode.

A POST to a path ending in ``/messages`` is answered the way Anthropic answers
``/v1/messages``: a ``tool_use`` block for a tool call, a ``text`` block
otherwise. Point ``ChatAnthropic`` at :attr:`FakeLLMServer.anthropic_base_url`.
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
    ``completion_tokens_details.reasoning_tokens``. A rule answering an
    Anthropic request returns it as Anthropic's ``usage`` object instead
    (``input_tokens``, ``cache_read_input_tokens``, ...).
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


DEFAULT_ANTHROPIC_USAGE: Dict[str, Any] = {"input_tokens": 1, "output_tokens": 1}


def classify_anthropic_request(body: Dict[str, Any]) -> tuple:
    """``(name, prompt_text)`` for an Anthropic ``/v1/messages`` request body.

    ``name`` is the first tool's name, or ``"plain"``; ``prompt_text`` joins the
    system prompt and every message's text.
    """
    tools = body.get("tools") or []
    name = tools[0]["name"] if tools else "plain"

    def text_of(content: Any) -> str:
        if isinstance(content, list):
            return "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
        return str(content or "")

    parts = [text_of(body.get("system"))] + [text_of(m.get("content")) for m in body.get("messages", [])]
    return name, "\n".join(parts)


def anthropic_message(body: Dict[str, Any], name: str, payload: Any,
                      usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """An Anthropic ``message`` answering ``body``: a tool_use block, or text for ``plain``."""
    if name == "plain":
        content, stop = [{"type": "text", "text": str(payload)}], "end_turn"
    else:
        tool_input = json.loads(payload) if isinstance(payload, str) else payload
        content, stop = [{"type": "tool_use", "id": "toolu_fake", "name": name, "input": tool_input}], "tool_use"
    return {"id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model", "fake"),
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": usage or DEFAULT_ANTHROPIC_USAGE}


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

    @property
    def anthropic_base_url(self) -> str:
        """The root the Anthropic client appends ``/v1/messages`` to."""
        assert self._server is not None, "call start() first"
        return f"http://{self.host}:{self._server.server_address[1]}"

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

            def _anthropic(self, body: Dict[str, Any]) -> None:
                name, text = classify_anthropic_request(body)
                call = {"name": name, "mode": "anthropic", "body": body, "api_key": self.headers.get("x-api-key")}
                if outer.reject_temperature and "temperature" in body:
                    outer.calls.append({**call, "matched": False})
                    self._send(400, {"type": "error", "error": {
                        "type": "invalid_request_error",
                        "message": "temperature: this model does not support sampling parameters."}})
                    return
                rule = next(
                    (r for r in outer.rules if r.name == name and (r.when is None or r.when in text)),
                    None,
                )
                outer.calls.append({**call, "matched": rule is not None})
                if rule is None:
                    self._send(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                "message": f"fake llm: no rule for {name}"}})
                    return
                payload = rule.payload(text) if callable(rule.payload) else rule.payload
                self._send(200, anthropic_message(body, name, payload, rule.usage))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.rstrip("/").endswith("/messages"):
                    self._anthropic(body)
                    return
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
