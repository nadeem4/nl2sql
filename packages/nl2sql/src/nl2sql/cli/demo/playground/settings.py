"""The playground's settings panel: an API key and a model per LLM node.

The panel has no store of its own. It edits the demo project's
``configs/llm.demo.yaml`` and ``.env.demo`` -- the files ``nl2sql demo`` and
the rest of the CLI read -- and then reloads the engine's LLM registry from
that same YAML, so a change takes effect on the next question with no restart.

Three rules:

* **Local only by default.** The playground has no login. On a host other
  machines can reach, anyone who opens the page could swap in their own key or
  spend on the owner's, so settings are refused there unless ``nl2sql demo
  --allow-settings`` says otherwise.
* **One source of truth.** The two files above, nothing else.
* **Secrets are write-only.** A key goes in; only ``mask_key``'s form comes
  back, in responses and in errors alike.

``hosted`` is a fourth state rather than a loosening of the first rule: on a
public demo (:mod:`nl2sql.cli.demo.playground.hosted`) there is nothing to save,
because the server keeps no key and writes no config. The panel then reports
itself unavailable with ``hosted: True``, and the page offers the visitor a key
form that writes to their own browser instead.

The engine builds its pipeline, and fetches its LLM clients, per question. A
settings change therefore waits, under :class:`RunGate`, for the questions in
flight to finish on the clients they started with, and holds new ones back
until the registry is reloaded.
"""
from __future__ import annotations

import ipaddress
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request

from nl2sql.llm.providers import (
    KEY_SHAPE_MESSAGE,
    LLM_AGENTS,
    PROVIDER_KEYS,
    VERIFIED_MODELS,
    env_var_for_key,
    looks_like_api_key,
    mask_key,
    provider_for_key,
)
from nl2sql.cli.demo.llm_config import persist_api_key, point_llm_config_at
from nl2sql.common.logger import get_logger
from nl2sql.llm.registry import PROVIDER_PRESETS

logger = get_logger(__name__)

LLM_CONFIG = Path("configs") / "llm.demo.yaml"
ENV_FILE = Path(".env.demo")

# What each LLM node does, for the page. The nodes and their agent names are
# ``LLM_AGENTS``; anything not listed there uses ``default``.
_NODE_TEXT = {
    "datasource_resolver": ("Answerability check",
                            "Refuses a question the connected data cannot answer, before anything else runs. "
                            "A short prompt."),
    "decomposer": ("Question splitter", "Breaks the question into sub-queries. A short prompt."),
    "ast_planner": ("Query planner",
                    "Turns each sub-query into a plan. Decides whether the SQL is right; most of the tokens."),
    "refiner": ("Plan repair", "Rewrites a plan the validator rejected. Runs only on a retry."),
    "answer_synthesizer": ("Answer writer", "Summarises the rows in a sentence. A short prompt."),
}
LLM_NODES: List[Dict[str, str]] = [
    {"agent": agent, "label": _NODE_TEXT[node][0], "does": _NODE_TEXT[node][1]}
    for node, agent in LLM_AGENTS.items()
]
_AGENTS = {node["agent"] for node in LLM_NODES}

PROVIDER_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic", "openrouter": "OpenRouter", "ollama": "Ollama"}

# Why the panel writes nothing on a hosted demo. The page turns this into the
# browser-only key form rather than an "off" notice; see ``read``.
HOSTED_SETTINGS_REASON = (
    "This is the hosted demo, so nothing is saved on the server: your key lives in this browser "
    "tab, travels with each question, and is used only to answer it."
)


def is_loopback(host: str) -> bool:
    """True when ``host`` is reachable from this machine only."""
    name = (host or "").strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


class RunGate:
    """Lets many questions run at once, or one settings change, never both.

    A change waits for the questions in flight and holds new ones back until
    it is done, so no question sees half a configuration.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._runs = 0
        self._changing = False

    @contextmanager
    def run(self) -> Iterator[None]:
        with self._cond:
            while self._changing:
                self._cond.wait()
            self._runs += 1
        try:
            yield
        finally:
            with self._cond:
                self._runs -= 1
                self._cond.notify_all()

    @contextmanager
    def change(self) -> Iterator[None]:
        with self._cond:
            while self._changing:
                self._cond.wait()
            self._changing = True
            while self._runs:
                self._cond.wait()
        try:
            yield
        finally:
            with self._cond:
                self._changing = False
                self._cond.notify_all()


class SettingsPanel:
    """Reads and writes the demo project's LLM settings for the playground."""

    def __init__(self, engine, project_dir: Optional[Path], mode: str, host: str,
                 allow_settings: bool, hosted: bool = False) -> None:
        self.engine = engine
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self.mode = mode
        self.host = host
        self.hosted = hosted
        self.gate = RunGate()
        if hosted:
            # A third state, not a loosened gate: there is nothing to save,
            # because the server keeps no key and writes no config.
            self.reason: Optional[str] = HOSTED_SETTINGS_REASON
        elif self.project_dir is None:
            self.reason = "Settings are available in the playground that nl2sql demo starts."
        elif not (is_loopback(host) or allow_settings):
            self.reason = (
                f"This playground is bound to {host}, which other "
                "machines can reach, and it has no login: anyone who can open this page could "
                "swap in their own key or run up costs on yours. Restart it on 127.0.0.1, or "
                "pass --allow-settings if you trust everyone who can reach it."
            )
        else:
            self.reason = None
        self.loopback_only = self.reason is None and not allow_settings

    @property
    def available(self) -> bool:
        return self.reason is None

    # --- the guard -------------------------------------------------------------

    def guard(self, request: Request) -> None:
        """Refuses a settings change the rules do not allow.

        Beyond rule (a), a change must be a same-origin JSON request: another
        page open in the same browser can post to a loopback port, but not with
        a JSON content type (that needs a CORS preflight, which is never
        granted), not with its own Origin, and -- on a loopback bind -- not
        through a DNS name rebound to 127.0.0.1.
        """
        self.guard_read(request)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="Send settings as JSON.")

    def guard_read(self, request: Request) -> None:
        """The guard without the JSON rule, for a read of local-only data (the feedback list)."""
        if not self.available:
            raise HTTPException(status_code=403, detail=self.reason)
        host_header = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != host_header.lower():
            raise HTTPException(status_code=403, detail="Settings can be changed only from the playground page itself.")
        if self.loopback_only and not is_loopback(urlsplit(f"//{host_header}").hostname or ""):
            raise HTTPException(status_code=403, detail="Open the playground as localhost or 127.0.0.1 to change settings.")

    # --- reading ---------------------------------------------------------------

    @property
    def _llm_path(self) -> Path:
        return self.project_dir / LLM_CONFIG

    def _load(self) -> Dict[str, Any]:
        return yaml.safe_load(self._llm_path.read_text(encoding="utf-8")) or {}

    def read(self) -> Dict[str, Any]:
        """What the panel shows. Never the key: at most its masked form."""
        if not self.available:
            # ``hosted`` distinguishes "off, and here is why" from "there is
            # nothing to save here, keep your key in the browser".
            return {"available": False, "hosted": self.hosted, "reason": self.reason}

        cfg = self._load()
        default = cfg.get("default") or {}
        provider = default.get("provider", "openai")
        agents = cfg.get("agents") or {}
        verified = VERIFIED_MODELS.get(provider, {})

        return {
            "available": True,
            "mode": self.mode,
            "provider": provider,
            "default_model": default.get("model"),
            "key": self._key(provider),
            "models": [{"id": model, "temperature": temp} for model, temp in verified.items()],
            "models_note": None if verified else (
                f"Model lists exist for OpenAI and Anthropic only for now. Every node uses "
                f"{default.get('model')} from {LLM_CONFIG.as_posix()}."
            ),
            # Every provider a node can be put on, one key each.
            "providers": [
                {"id": name, "label": PROVIDER_LABELS.get(name, name), **self._key(name),
                 "usable": self._usable(name, provider),
                 "models": [{"id": model, "temperature": temp} for model, temp in models.items()]}
                for name, models in VERIFIED_MODELS.items()
            ],
            "nodes": [self._node(node, agents.get(node["agent"]) or {}, provider) for node in LLM_NODES],
            "files": {"llm": LLM_CONFIG.as_posix(), "env": ENV_FILE.as_posix()},
        }

    def _key(self, provider: str) -> Dict[str, Optional[str]]:
        """A provider's key as the panel may show it: masked, and the variable it is in."""
        preset = PROVIDER_PRESETS.get(provider)
        env_var = preset.api_key_env if preset else None
        active = os.environ.get(env_var) if (env_var and self.mode == "live") else None
        return {"masked": mask_key(active) if active else None, "env_var": env_var}

    def _usable(self, provider: str, default_provider: str) -> bool:
        """Whether a node can run on ``provider`` right now.

        The default's provider always can: in replay mode it is the recording
        server. Any other provider needs its own key, in live mode.
        """
        return provider == default_provider or self._key(provider)["masked"] is not None

    def _node(self, node: Dict[str, str], entry: Dict[str, Any], default_provider: str) -> Dict[str, Any]:
        provider = entry.get("provider") if entry else None
        unavailable = None
        if provider and not self._usable(provider, default_provider):
            label = PROVIDER_LABELS.get(provider, provider)
            unavailable = (f"{label} has no key, so this step cannot run. Save a key for {label} above, "
                           "or choose another provider for it.")
        return {**node, "provider": provider, "model": entry.get("model") if entry else None,
                "unavailable": unavailable}

    # --- writing ---------------------------------------------------------------

    def _reload(self) -> None:
        """Rebuilds the registry from the YAML the CLI reads."""
        self.engine.reload_llm_config(self._llm_path)

    def save_key(self, key: str) -> None:
        """Saves ``key`` to ``.env.demo`` and switches the demo to live on it."""
        key = (key or "").strip()
        if not looks_like_api_key(key):
            # Deliberately says nothing about the value it was given.
            raise HTTPException(status_code=400, detail=KEY_SHAPE_MESSAGE)
        variable = env_var_for_key(key)
        with self.gate.change():
            try:
                persist_api_key(self.project_dir / ENV_FILE, key)
                os.environ[variable] = key
                if self.mode != "live":
                    # The replay placeholder in OPENAI_API_KEY is not a key and
                    # must not be sent to a real provider. Live, other
                    # providers' keys stay: a node may be on one of them.
                    for other in PROVIDER_KEYS:
                        if other != variable:
                            os.environ.pop(other, None)
                point_llm_config_at(self.project_dir, None, provider=provider_for_key(key))
                self._reload()
            except Exception as exc:
                # The exception's text may quote the key; only its type is kept.
                logger.error("Saving the API key failed (%s)", type(exc).__name__)
                raise HTTPException(
                    status_code=500,
                    detail="The key could not be saved. Check that the demo project folder is writable.",
                ) from None
            self.mode = "live"

    def set_models(self, models: Dict[str, Any]) -> None:
        """Writes a provider and a model per node into ``llm.demo.yaml``.

        Each value is ``None`` (use the default agent), a model name (on the
        default's provider), or ``{"provider": ..., "model": ...}``.
        """
        default_provider = (self._load().get("default") or {}).get("provider", "openai")
        choices = {agent: self._choice(agent, value, default_provider) for agent, value in models.items()}

        with self.gate.change():
            cfg = self._load()
            default = cfg.get("default") or {}
            agents = cfg.get("agents") or {}
            for agent, choice in choices.items():
                if choice is None:
                    agents.pop(agent, None)
                    continue
                provider, model = choice
                if provider == default.get("provider"):
                    # On the default's provider a node differs from the default
                    # only in its model and the temperature that model accepts;
                    # endpoint and key follow the default.
                    api_key, base_url = default.get("api_key"), default.get("base_url")
                else:
                    api_key, base_url = "${env:" + PROVIDER_PRESETS[provider].api_key_env + "}", None
                entry = {"provider": provider, "model": model,
                         "temperature": VERIFIED_MODELS[provider][model], "api_key": api_key,
                         "base_url": base_url, "name": agent}
                agents[agent] = {k: v for k, v in entry.items() if v is not None or k == "temperature"}
            cfg["agents"] = agents
            self._llm_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            self._reload()

    def _choice(self, agent: str, value: Any, default_provider: str) -> Optional[tuple]:
        """Validates one node's choice as ``(provider, model)``, or None for the default."""
        if agent not in _AGENTS:
            raise HTTPException(status_code=400, detail=f"'{agent}' is not an LLM node.")
        if value is None:
            return None
        if isinstance(value, dict):
            provider, model = value.get("provider") or default_provider, value.get("model")
        else:
            provider, model = default_provider, value
        verified = VERIFIED_MODELS.get(provider)
        if not verified:
            raise HTTPException(
                status_code=400,
                detail=f"Per-node models can be chosen for OpenAI and Anthropic only for now, not {provider}.",
            )
        if model not in verified:
            raise HTTPException(
                status_code=400,
                detail=f"'{model}' is not on the verified list for {provider}: {', '.join(verified)}.",
            )
        if not self._usable(provider, default_provider):
            label = PROVIDER_LABELS.get(provider, provider)
            raise HTTPException(status_code=400, detail=f"Save a key for {label} before putting a step on it.")
        return provider, model
