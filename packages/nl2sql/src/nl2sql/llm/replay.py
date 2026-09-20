"""Recorded LLM responses for key-free demo mode.

A :class:`ReplayStore` is a JSON file of model outputs captured once from a real
provider by :class:`RecordingProxy`. Its :meth:`ReplayStore.rules` feeds
:class:`nl2sql.testing.fake_llm.FakeLLMServer`, which then answers the pipeline
without an API key.

Recordings are keyed by the structured-output name plus the question text found
in the prompt, so the same store can hold a different plan for every guided
question. :func:`extract_question` finds that question, using the markers the
real prompt templates put in front of it.
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterable, List, Optional, Tuple

from nl2sql.testing.fake_llm import Rule, classify_request

# The markers each real prompt template puts in front of the user question (or,
# for the planner, the sub-query intent). Each captures the first non-empty line
# after its marker. Order matters: the planner's `[USER_QUERY]` is tried first
# because its `[EXAMPLES]` block contains `User Query: "..."` lines of its own,
# which the later markers would otherwise match.
QUESTION_MARKERS = (
    # ast_planner/prompts.py: "[USER_QUERY]\n{user_query}"
    re.compile(r"\[USER_QUERY\][ \t]*\r?\n(?:[ \t]*\r?\n)*[ \t]*(\S[^\r\n]*)"),
    # decomposer/prompts.py: "User Query:\n{user_query}"
    re.compile(r"^User Query:[ \t]*\r?\n(?:[ \t]*\r?\n)*[ \t]*(\S[^\r\n]*)", re.MULTILINE),
    # answer_synthesizer/prompts.py: "User Query: {user_query}"
    re.compile(r"^User Query:[ \t]+(\S[^\r\n]*)", re.MULTILINE),
)


def extract_question(prompt_text: str) -> Optional[str]:
    """Returns the question a rendered prompt was built around, if any."""
    for marker in QUESTION_MARKERS:
        match = marker.search(prompt_text)
        if match:
            return match.group(1).strip()
    return None


@dataclass
class Recording:
    name: str                 # "DecomposerResponse" | "PlanModel" | "AggregatedResponse" | "plain"
    when: Optional[str]       # question text that must appear in the prompt, or None
    payload: Any              # dict for structured outputs, str for plain


class ReplayStore:
    """An ordered collection of :class:`Recording`, unique by ``(name, when)``."""

    def __init__(self, recordings: Iterable[Recording] = ()):
        self._recordings: List[Recording] = []
        self._index: Dict[Tuple[str, Optional[str]], int] = {}
        for recording in recordings:
            self.add(recording)

    @classmethod
    def load(cls, path: pathlib.Path) -> "ReplayStore":
        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return cls(Recording(r["name"], r.get("when"), r["payload"]) for r in raw)

    def save(self, path: pathlib.Path) -> None:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"name": r.name, "when": r.when, "payload": r.payload} for r in self._recordings]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def add(self, recording: Recording) -> None:
        """Adds a recording, replacing any existing one with the same ``(name, when)``."""
        key = (recording.name, recording.when)
        existing = self._index.get(key)
        if existing is None:
            self._index[key] = len(self._recordings)
            self._recordings.append(recording)
        else:
            self._recordings[existing] = recording

    def rules(self) -> List[Rule]:
        """Rules for ``FakeLLMServer``, question-specific ones first.

        The fake server takes the first matching rule, so a recording made for
        one question has to be offered before the catch-all made with
        ``when=None``.
        """
        specific = [r for r in self._recordings if r.when is not None]
        catch_all = [r for r in self._recordings if r.when is None]
        return [Rule(name=r.name, payload=r.payload, when=r.when) for r in specific + catch_all]


class RecordingProxy:
    """An OpenAI-compatible endpoint that forwards upstream and records the answer."""

    def __init__(self, upstream_base_url: str, upstream_api_key: str, store: ReplayStore,
                 host: str = "127.0.0.1", port: int = 0):
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.upstream_api_key = upstream_api_key
        self.store = store
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "call start() first"
        return f"http://{self.host}:{self._server.server_address[1]}/v1"

    def start(self) -> "RecordingProxy":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                status, content_type, response_body = outer._forward(raw)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def _forward(self, raw: bytes) -> Tuple[int, str, bytes]:
        request = urllib.request.Request(
            self.upstream_base_url + "/chat/completions",
            data=raw,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.upstream_api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
                body = response.read()
        except urllib.error.HTTPError as exc:  # upstream said no; pass it through verbatim
            return exc.code, exc.headers.get("Content-Type", "application/json"), exc.read()

        if status == 200:
            self._record(raw, body)
        return status, content_type, body

    def _record(self, raw: bytes, body: bytes) -> None:
        """Stores the upstream payload, keyed exactly as the replay server dispatches."""
        try:
            mode, name, prompt_text = classify_request(json.loads(raw))
            message = json.loads(body)["choices"][0]["message"]
            if mode == "tools":
                payload = json.loads(message["tool_calls"][0]["function"]["arguments"])
            elif mode == "json_schema":
                payload = json.loads(message["content"])
            else:
                payload = message["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            # A response shape we cannot key is not worth failing the run over:
            # the caller is recording, and a missing recording shows up as a
            # replay miss rather than as a crashed provider call.
            return
        self.store.add(Recording(name, extract_question(prompt_text), payload))

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
