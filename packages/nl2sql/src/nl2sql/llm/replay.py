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

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from nl2sql.common.errors import ReplayMiss
from nl2sql.testing.fake_llm import Rule, classify_anthropic_request, classify_request

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
    name: str                 # "DecomposerResponse" | "PlannerAnswer" | "AggregatedResponse" | "plain"
    when: Optional[str]       # question text that must appear in the prompt, or None
    payload: Any              # dict for structured outputs, str for plain


class ReplayStore:
    """An ordered collection of :class:`Recording`, unique by ``(name, when)``.

    ``provider`` and ``model`` name what answered when the recordings were
    made, so a replayed run can say which model produced it rather than the
    model the replaying config happens to name. On disk the store is
    ``{"provider", "model", "recordings": [...]}``; a bare list, the format
    before the model was recorded, still loads, naming no model.
    """

    def __init__(self, recordings: Iterable[Recording] = (), provider: Optional[str] = None,
                 model: Optional[str] = None):
        self._recordings: List[Recording] = []
        self._index: Dict[Tuple[str, Optional[str]], int] = {}
        self.provider = provider
        self.model = model
        for recording in recordings:
            self.add(recording)

    @property
    def recorded_with(self) -> Optional[Dict[str, Optional[str]]]:
        """``{"provider", "model"}`` the recordings came from, or None when unknown."""
        if not self.model:
            return None
        return {"provider": self.provider, "model": self.model}

    @classmethod
    def load(cls, path: pathlib.Path) -> "ReplayStore":
        return cls.from_json(pathlib.Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_json(cls, text: str) -> "ReplayStore":
        raw = json.loads(text)
        meta: Dict[str, Any] = {}
        if isinstance(raw, dict):
            meta, raw = raw, raw.get("recordings") or []
        return cls((Recording(r["name"], r.get("when"), r["payload"]) for r in raw),
                   provider=meta.get("provider"), model=meta.get("model"))

    def save(self, path: pathlib.Path) -> None:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"provider": self.provider, "model": self.model,
                   "recordings": [{"name": r.name, "when": r.when, "payload": r.payload}
                                  for r in self._recordings]}
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

    def discard(self, question: str) -> None:
        """Drops every recording made for ``question``; catch-alls (``when=None``) stay."""
        self._recordings = [r for r in self._recordings if r.when != question]
        self._index = {(r.name, r.when): i for i, r in enumerate(self._recordings)}

    def covered(self, questions: Iterable[str]) -> List[str]:
        """The ``questions`` this store can start answering.

        A generated answer begins with the decomposer, so a question without a
        decomposer recording keyed on it misses however much else is recorded.
        A verified answer never reaches the decomposer: its run records only
        the ``VerifiedQueryChoice`` that picked a query, so that counts too.
        """
        recorded = {r.when for r in self._recordings if r.name == "DecomposerResponse"}
        recorded |= {r.when for r in self._recordings if r.name == "VerifiedQueryChoice"
                     and (r.payload or {}).get("query_id") not in (None, "none_of_these")}
        return [q for q in questions if q in recorded]

    def find(self, name: str, prompt_text: str) -> Optional[Recording]:
        """The recording a call named ``name`` with this prompt replays, or None.

        The first recording made for a question the prompt contains, else the
        catch-all (``when=None``). The same order ``rules()`` gives the fake server.
        """
        for recording in self._recordings:
            if recording.name == name and recording.when is not None and recording.when in prompt_text:
                return recording
        return next((r for r in self._recordings if r.name == name and r.when is None), None)

    def rules(self) -> List[Rule]:
        """Rules for ``FakeLLMServer``, question-specific ones first.

        The fake server takes the first matching rule, so a recording made for
        one question has to be offered before the catch-all made with
        ``when=None``.
        """
        specific = [r for r in self._recordings if r.when is not None]
        catch_all = [r for r in self._recordings if r.when is None]
        return [Rule(name=r.name, payload=r.payload, when=r.when) for r in specific + catch_all]


def _text_of(messages: List[BaseMessage]) -> str:
    """The prompt as one string, the way the fake server reads a request."""
    parts = []
    for message in messages:
        content = message.content
        if isinstance(content, list):
            content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
        parts.append(str(content))
    return "\n".join(parts)


class ReplayChatModel(BaseChatModel):
    """A chat model that answers from a :class:`ReplayStore`: no key, no network (D209).

    An answer is found by the structured-output name (the schema's class name,
    ``plain`` for text) and the question the prompt carries. A call with no
    recording raises :class:`~nl2sql.common.errors.ReplayMiss`. The answer
    lands whole: nothing is streamed, and no token usage is reported.
    ``model`` is only the fallback label; the store's own model names the call.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: Any
    model: str = "replay"

    @property
    def _llm_type(self) -> str:
        return "replay"

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> Any:
        from langchain_core.output_parsers.openai_tools import JsonOutputKeyToolsParser, PydanticToolsParser

        name = schema.get("title", "the schema") if isinstance(schema, dict) else schema.__name__
        parser = (JsonOutputKeyToolsParser(key_name=name, first_tool_only=True) if isinstance(schema, dict)
                  else PydanticToolsParser(tools=[schema], first_tool_only=True))
        return self.bind(schema_name=name) | parser

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                  schema_name: Optional[str] = None, **_kwargs: Any) -> ChatResult:
        text = _text_of(messages)
        recording = self.store.find(schema_name or "plain", text)
        if recording is None:
            raise ReplayMiss(schema_name or "plain", extract_question(text))
        meta = {"model_name": (self.store.model or self.model), "model_provider": "replay"}
        if schema_name:
            call = {"name": schema_name, "args": recording.payload, "id": "replay_call", "type": "tool_call"}
            message = AIMessage(content="", tool_calls=[call], response_metadata=meta)
        else:
            payload = recording.payload
            message = AIMessage(content=payload if isinstance(payload, str) else json.dumps(payload),
                                response_metadata=meta)
        return ChatResult(generations=[ChatGeneration(message=message)])


class ReplayProvider:
    """The ``replay`` LLM provider: the recordings that ship with the engine, or ``store``.

    Registered under the ``nl2sql.llms`` entry point group as :data:`REPLAY`.
    It needs no key and no client library.
    """

    interface_version = 2
    label = "Recorded answers"
    api_key_env = None
    requires: Tuple[str, ...] = ()
    install_hint = ""
    structured_output = "tool_calling"

    def __init__(self, store: Optional[ReplayStore] = None) -> None:
        self._store = store

    @property
    def store(self) -> ReplayStore:
        """The recordings in use, the packaged ones unless a store was given."""
        if self._store is None:
            self._store = packaged_store()
        return self._store

    def create(self, agent: Any) -> ReplayChatModel:
        return ReplayChatModel(store=self.store, model=agent.model)


def packaged_store() -> ReplayStore:
    """Every recording file in ``nl2sql/datasets/recordings``, as one store."""
    from importlib.resources import files

    merged = ReplayStore()
    for entry in sorted(files("nl2sql.datasets").joinpath("recordings").iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".json"):
            continue
        part = ReplayStore.from_json(entry.read_text(encoding="utf-8"))
        merged.provider, merged.model = merged.provider or part.provider, merged.model or part.model
        for recording in part._recordings:
            merged.add(recording)
    return merged


class _Tap(BaseCallbackHandler):
    """Writes each model call's answer into a store, keyed by what the replay provider looks it up by."""

    def __init__(self, store: ReplayStore) -> None:
        self._store = store
        self._prompts: Dict[Any, str] = {}

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any, **_kwargs: Any) -> None:
        self._prompts[run_id] = _text_of(messages[0])

    def on_llm_end(self, response: Any, *, run_id: Any, **_kwargs: Any) -> None:
        prompt = self._prompts.pop(run_id, "")
        message = response.generations[0][0].message
        question = extract_question(prompt)
        if message.tool_calls:
            call = message.tool_calls[0]
            self._store.add(Recording(call["name"], question, call["args"]))
        else:
            self._store.add(Recording("plain", question, message.content))


class Recorder:
    """An LLM provider that calls ``upstream`` and keeps what it answered, for the replay provider to replay.

    Internal, for ``scripts/record_demo_answers.py``. Register it under any
    name; it takes the upstream's key variable and install hints, so the
    config resolves a key as it would for the upstream. ``store`` collects a
    recording per call, keyed by the structured-output name and the question
    the prompt carries; ``store.provider`` and ``store.model`` name what answered.
    """

    def __init__(self, upstream: Any, store: Optional[ReplayStore] = None, *, provider: Optional[str] = None) -> None:
        self.upstream = upstream
        self.store = store if store is not None else ReplayStore()
        self.provider = provider
        self.interface_version = upstream.interface_version
        self.label = upstream.label
        self.api_key_env = upstream.api_key_env
        self.requires = upstream.requires
        self.install_hint = upstream.install_hint
        self.structured_output = getattr(upstream, "structured_output", "tool_calling")

    def create(self, agent: Any) -> Any:
        client = self.upstream.create(agent)
        self.store.provider = self.provider or self.store.provider
        self.store.model = agent.model
        client.callbacks = [*(client.callbacks or []), _Tap(self.store)]
        return client


REPLAY = ReplayProvider()


class RecordingProxy:
    """An endpoint that forwards upstream and records the answer.

    ``wire`` is the protocol the upstream speaks. ``"openai"`` (the default)
    takes ``POST /v1/chat/completions`` and forwards it to
    ``upstream_base_url + "/chat/completions"``; ``"anthropic"`` takes
    ``POST /v1/messages`` and forwards it to ``upstream_base_url + "/v1/messages"``
    with the key in ``x-api-key``. Either way a recording is keyed by the
    structured-output name and the question, so a store recorded on one wire
    replays on the other: the replay server answers both.

    ``provider`` is the provider behind the upstream (OpenRouter speaks the
    OpenAI wire); it defaults to the wire's own. Each answer recorded stamps
    the store with it and with the model the request asked for.
    """

    def __init__(self, upstream_base_url: str, upstream_api_key: str, store: ReplayStore,
                 host: str = "127.0.0.1", port: int = 0, wire: str = "openai",
                 provider: Optional[str] = None):
        if wire not in ("openai", "anthropic"):
            raise ValueError(f"Unknown wire '{wire}': expected 'openai' or 'anthropic'.")
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.upstream_api_key = upstream_api_key
        self.provider = provider or wire
        self.store = store
        self.host = host
        self.port = port
        self.wire = wire
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        """The OpenAI-wire endpoint (what ``ChatOpenAI`` takes as ``base_url``)."""
        assert self._server is not None, "call start() first"
        return f"http://{self.host}:{self._server.server_address[1]}/v1"

    @property
    def anthropic_base_url(self) -> str:
        """The root the Anthropic client appends ``/v1/messages`` to."""
        assert self._server is not None, "call start() first"
        return f"http://{self.host}:{self._server.server_address[1]}"

    def start(self) -> "RecordingProxy":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if outer.wire == "anthropic":
                    status, content_type, response_body = outer._forward_anthropic(
                        raw, self.headers.get("anthropic-version"), self.headers.get("anthropic-beta"))
                else:
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

    def _forward_anthropic(self, raw: bytes, version: Optional[str],
                           beta: Optional[str]) -> Tuple[int, str, bytes]:
        headers = {"Content-Type": "application/json", "x-api-key": self.upstream_api_key,
                   "anthropic-version": version or "2023-06-01"}
        if beta:
            headers["anthropic-beta"] = beta
        request = urllib.request.Request(self.upstream_base_url + "/v1/messages", data=raw,
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
                body = response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", "application/json"), exc.read()

        if status == 200:
            self._record_anthropic(raw, body)
        return status, content_type, body

    def _record_anthropic(self, raw: bytes, body: bytes) -> None:
        """Stores an Anthropic answer under the same key an OpenAI one would get."""
        try:
            request = json.loads(raw)
            name, prompt_text = classify_anthropic_request(request)
            content = json.loads(body)["content"]
            if name == "plain":
                payload: Any = "".join(block.get("text") or "" for block in content
                                       if block.get("type") == "text")
            else:
                payload = next(block["input"] for block in content if block.get("type") == "tool_use")
        except (KeyError, TypeError, ValueError, StopIteration):
            return
        self._add(request, Recording(name, extract_question(prompt_text), payload))

    def _add(self, request: Any, recording: Recording) -> None:
        """Stores ``recording``, stamping the store with who answered it."""
        model = request.get("model") if isinstance(request, dict) else None
        if model:
            self.store.provider, self.store.model = self.provider, model
        self.store.add(recording)

    def _record(self, raw: bytes, body: bytes) -> None:
        """Stores the upstream payload, keyed exactly as the replay server dispatches."""
        try:
            request = json.loads(raw)
            mode, name, prompt_text = classify_request(request)
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
        self._add(request, Recording(name, extract_question(prompt_text), payload))

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
