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


@dataclass
class FakeLLMServer:
    rules: List[Rule]
    host: str = "127.0.0.1"
    port: int = 0
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

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mode, name, text = classify_request(body)
                rule = next(
                    (r for r in outer.rules if r.name == name and (r.when is None or r.when in text)),
                    None,
                )
                outer.calls.append({"name": name, "mode": mode, "matched": rule is not None})
                if rule is None:
                    out = json.dumps({"error": {"message": f"fake llm: no rule for {name}"}}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                payload = rule.payload(text) if callable(rule.payload) else rule.payload
                if mode == "tools":
                    msg = {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(payload)}}]}
                    finish = "tool_calls"
                elif mode == "json_schema":
                    msg, finish = {"role": "assistant", "content": json.dumps(payload)}, "stop"
                else:
                    msg, finish = {"role": "assistant", "content": str(payload)}, "stop"
                resp = {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                        "model": body.get("model", "fake"),
                        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                        "usage": rule.usage or DEFAULT_USAGE}
                out = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
