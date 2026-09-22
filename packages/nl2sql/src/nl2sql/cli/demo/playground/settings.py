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

The engine builds its pipeline, and fetches its LLM clients, per question. A
settings change therefore waits, under :class:`RunGate`, for the questions in
flight to finish on the clients they started with, and holds new ones back
until the registry is reloaded.
"""
from __future__ import annotations

import ipaddress
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request

from nl2sql.cli.common.api_key import VERIFIED_MODELS, env_var_for_key, mask_key, provider_for_key
from nl2sql.cli.commands.demo import PROVIDER_KEYS, _persist_api_key, _point_llm_config_at
from nl2sql.common.logger import get_logger
from nl2sql.configs import ConfigManager
from nl2sql.llm.registry import PROVIDER_PRESETS

logger = get_logger(__name__)

LLM_CONFIG = Path("configs") / "llm.demo.yaml"
ENV_FILE = Path(".env.demo")

# The five nodes that call a model, by the agent name the LLM registry knows
# them under, labelled by what they do. Anything not listed uses ``default``.
LLM_NODES: List[Dict[str, str]] = [
    {"agent": "datasourceresolver", "label": "Answerability check",
     "does": "Refuses a question the connected data cannot answer, before anything else runs. A short prompt."},
    {"agent": "decomposer", "label": "Question splitter",
     "does": "Breaks the question into sub-queries. A short prompt."},
    {"agent": "astplanner", "label": "Query planner",
     "does": "Turns each sub-query into a plan. Decides whether the SQL is right; most of the tokens."},
    {"agent": "refiner", "label": "Plan repair",
     "does": "Rewrites a plan the validator rejected. Runs only on a retry."},
    {"agent": "answersynthesizer", "label": "Answer writer",
     "does": "Summarises the rows in a sentence. A short prompt."},
]
_AGENTS = {node["agent"] for node in LLM_NODES}

# Letters, digits, '-' and '_' only: the key is written into a dotenv file, so
# nothing that could end the line or start a comment gets through.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


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
                 allow_settings: bool) -> None:
        self.engine = engine
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self.mode = mode
        self.host = host
        self.gate = RunGate()
        if self.project_dir is None:
            self.reason: Optional[str] = (
                "Settings are available in the playground that nl2sql demo starts."
            )
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
        if not self.available:
            raise HTTPException(status_code=403, detail=self.reason)
        host_header = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != host_header.lower():
            raise HTTPException(status_code=403, detail="Settings can be changed only from the playground page itself.")
        if self.loopback_only and not is_loopback(urlsplit(f"//{host_header}").hostname or ""):
            raise HTTPException(status_code=403, detail="Open the playground as localhost or 127.0.0.1 to change settings.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="Send settings as JSON.")

    # --- reading ---------------------------------------------------------------

    @property
    def _llm_path(self) -> Path:
        return self.project_dir / LLM_CONFIG

    def _load(self) -> Dict[str, Any]:
        return yaml.safe_load(self._llm_path.read_text(encoding="utf-8")) or {}

    def read(self) -> Dict[str, Any]:
        """What the panel shows. Never the key: at most its masked form."""
        if not self.available:
            return {"available": False, "reason": self.reason}

        cfg = self._load()
        default = cfg.get("default") or {}
        provider = default.get("provider", "openai")
        agents = cfg.get("agents") or {}
        verified = VERIFIED_MODELS.get(provider, {})

        preset = PROVIDER_PRESETS.get(provider)
        env_var = preset.api_key_env if preset else None
        active = os.environ.get(env_var) if (env_var and self.mode == "live") else None

        return {
            "available": True,
            "mode": self.mode,
            "provider": provider,
            "default_model": default.get("model"),
            "key": {"masked": mask_key(active) if active else None, "env_var": env_var},
            "models": [{"id": model, "temperature": temp} for model, temp in verified.items()],
            "models_note": None if verified else (
                f"The model list is OpenAI-only for now. Every node uses "
                f"{default.get('model')} from {LLM_CONFIG.as_posix()}."
            ),
            "nodes": [
                {**node, "model": (agents.get(node["agent"]) or {}).get("model")}
                for node in LLM_NODES
            ],
            "files": {"llm": LLM_CONFIG.as_posix(), "env": ENV_FILE.as_posix()},
        }

    # --- writing ---------------------------------------------------------------

    def _reload(self) -> None:
        """Rebuilds the registry from the YAML the CLI reads."""
        cfg = ConfigManager().load_llm(self._llm_path)
        agents = dict(cfg.agents or {})
        agents["default"] = cfg.default
        self.engine.context.llm_registry.replace_llms(agents)

    def save_key(self, key: str) -> None:
        """Saves ``key`` to ``.env.demo`` and switches the demo to live on it."""
        key = (key or "").strip()
        if not _KEY_SHAPE.match(key):
            # Deliberately says nothing about the value it was given.
            raise HTTPException(
                status_code=400,
                detail="That does not look like an API key: expected 20 or more letters, digits, "
                       "'-' or '_', with no spaces.",
            )
        variable = env_var_for_key(key)
        with self.gate.change():
            try:
                _persist_api_key(self.project_dir / ENV_FILE, key)
                os.environ[variable] = key
                # The replay placeholder in OPENAI_API_KEY, or a key for the
                # other provider, must not be sent with the new provider.
                for other in PROVIDER_KEYS:
                    if other != variable:
                        os.environ.pop(other, None)
                _point_llm_config_at(self.project_dir, None, provider=provider_for_key(key))
                self._reload()
            except Exception as exc:
                # The exception's text may quote the key; only its type is kept.
                logger.error("Saving the API key failed (%s)", type(exc).__name__)
                raise HTTPException(
                    status_code=500,
                    detail="The key could not be saved. Check that the demo project folder is writable.",
                ) from None
            self.mode = "live"

    def set_models(self, models: Dict[str, Optional[str]]) -> None:
        """Writes one model per node into ``llm.demo.yaml``; ``None`` means the default."""
        cfg = self._load()
        default = cfg.get("default") or {}
        provider = default.get("provider", "openai")
        verified = VERIFIED_MODELS.get(provider)
        if not verified:
            raise HTTPException(
                status_code=400,
                detail=f"Per-node models can be chosen for OpenAI and Anthropic only for now; this demo uses {provider}.",
            )
        for agent, model in models.items():
            if agent not in _AGENTS:
                raise HTTPException(status_code=400, detail=f"'{agent}' is not an LLM node.")
            if model is not None and model not in verified:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{model}' is not on the verified list: {', '.join(verified)}.",
                )

        with self.gate.change():
            cfg = self._load()
            default = cfg.get("default") or {}
            agents = cfg.get("agents") or {}
            for agent, model in models.items():
                if model is None:
                    agents.pop(agent, None)
                    continue
                # A node differs from the default only in its model and the
                # temperature that model accepts; endpoint and key follow it.
                entry = {"provider": default.get("provider"), "model": model,
                         "temperature": verified[model], "api_key": default.get("api_key"),
                         "base_url": default.get("base_url"), "name": agent}
                agents[agent] = {k: v for k, v in entry.items() if v is not None or k == "temperature"}
            cfg["agents"] = agents
            self._llm_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            self._reload()
