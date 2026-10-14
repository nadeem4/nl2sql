import os
from contextvars import ContextVar
from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, Mapping, NamedTuple, Optional

from langchain_core.language_models import BaseChatModel

from nl2sql.secrets import SecretManager
from .client_cache import ClientKey, LLMClientCache
from .llm_set import LLMSet
from .models import AgentConfig
from .request_key import RequestLLMs, current_request_llms
from .wires import WIRES, Wire

if TYPE_CHECKING:
    from nl2sql.config import LLM
    from nl2sql.registry import Registry

# The set the run in this context reads (``LLMRegistry.begin_run``). The
# pipeline's clients are built while its graph is built, in the run's own
# thread, so a context variable is enough to give every node the same set.
_RUN_LLMS: ContextVar[Optional[LLMSet]] = ContextVar("nl2sql_run_llms", default=None)


class ProviderPreset(NamedTuple):
    """Endpoint, credential and wire-type defaults for one provider.

    Attributes:
        base_url: Endpoint the provider is reached on, or None to let the
            client resolve its own default (``openai_compatible`` has none:
            the config must give one).
        api_key_env: Environment variable named in errors and used as the
            last-resort source of the key.
        api_key_placeholder: Stand-in key for providers that may authenticate
            nothing. Non-None means the provider needs no real credential.
        wire: The wire type the provider speaks, a key of
            ``nl2sql.llm.wires.WIRES``. Its adapter builds the client.
        structured_output: How structured answers are asked for when the
            agent does not say: ``tool_calling``, ``json_schema`` or ``json_mode``.
        preset_of: The provider this one is a named preset of, built by the
            same code with this row's endpoint and key.
    """

    base_url: Optional[str]
    api_key_env: Optional[str]
    api_key_placeholder: Optional[str] = None
    wire: str = "openai"
    structured_output: str = "tool_calling"
    preset_of: Optional[str] = None


# The built-in providers (docs/architecture/phase2.md, section 5). ``openai``
# and ``anthropic`` are the two services with wires of their own: Anthropic
# speaks its Messages API, where prompt caching and reliable tool-based
# structured output work. ``openai_compatible`` is any other server with an
# OpenAI-style chat API (Azure OpenAI's v1 endpoint, vLLM, LiteLLM, a company
# gateway): the config gives its ``base_url``. ``openrouter`` and ``ollama``
# are named presets of it: a fixed endpoint and key variable, no code of their
# own; a config-supplied ``base_url`` still overrides the preset's.
#
# ChatOpenAI refuses to construct without an ``api_key`` (``openai.OpenAIError:
# Missing credentials``), so a provider that may need none sends a placeholder
# the server ignores. Never ``OPENAI_API_KEY``: that key belongs to OpenAI.
PROVIDER_PRESETS: Dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        base_url=None,
        api_key_env="OPENAI_API_KEY",
    ),
    "openrouter": ProviderPreset(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        preset_of="openai_compatible",
    ),
    "ollama": ProviderPreset(
        base_url="http://localhost:11434/v1",
        api_key_env=None,
        api_key_placeholder="ollama",
        preset_of="openai_compatible",
    ),
    "anthropic": ProviderPreset(
        base_url=None,
        api_key_env="ANTHROPIC_API_KEY",
        wire="anthropic",
    ),
    "openai_compatible": ProviderPreset(
        base_url=None,
        api_key_env=None,
        api_key_placeholder="not-needed",
        # Local servers support schema-guided decoding more often than tool calls.
        structured_output="json_schema",
    ),
}


# The fields an agent takes from the top of the ``llm`` section unless it sets its own.
AGENT_FIELDS = ("provider", "model", "temperature", "api_key", "base_url", "structured_output", "parse_retries",
                "max_tokens", "timeout_s", "max_retries", "headers")


def wire_for_provider(provider: str) -> Wire:
    """The adapter for the wire type ``provider`` speaks."""
    return WIRES[PROVIDER_PRESETS[provider].wire]


def agents_from_config(llm: "LLM") -> Dict[str, Any]:
    """The ``llm`` section as one entry per agent, keyed as the nodes ask ``get_llm``.

    ``default`` is the top level. Each agent in ``llm.agents`` takes every
    field it sets and inherits the rest (``model_fields_set``, so
    ``temperature: null`` still means "send none"). An entry is an
    ``AgentConfig``, or the chat model object when ``model`` is one.
    """
    from .providers import LLM_AGENTS

    def entry(name: str, fields: Dict[str, Any]) -> Any:
        if not isinstance(fields["model"], str):
            return fields["model"]
        return AgentConfig(name=name, **fields)

    top = {field: getattr(llm, field) for field in AGENT_FIELDS}
    agents = {"default": entry("default", top)}
    for agent, override in llm.agents.items():
        key = LLM_AGENTS.get(agent, agent)
        agents[key] = entry(key, {**top, **{field: getattr(override, field) for field in override.model_fields_set}})
    return agents


class RunLLMs:
    """The ``LLMSet`` one run reads, pinned to the run's context until ``end()``.

    ``unpin()`` stops this context reading the run's set; ``release()`` lets
    the engine retire it, and close clients only it held, once the run's
    graph has finished. ``end()`` does both. Each runs once.
    """

    def __init__(self, owner: "LLMRegistry", llms: LLMSet, token: Any):
        self.llms = llms
        self._owner = owner
        self._token = token
        self._released = False

    def unpin(self) -> None:
        if self._token is not None:
            _RUN_LLMS.reset(self._token)
            self._token = None

    def release(self) -> None:
        with self._owner._lock:
            if self._released:
                return
            self._released = True
        self._owner._end_run(self.llms)

    def end(self) -> None:
        self.unpin()
        self.release()


class LLMRegistry:
    """One engine's LLM: the current ``LLMSet``, and the ``LLMClientCache`` its clients come from.

    ``registry`` turns a provider name into its ``LLMProvider``; without one,
    the built-in providers are used. Each engine has its own, so nothing here
    is shared between engines.
    """

    def __init__(self, secret_manager: SecretManager, registry: Optional["Registry"] = None):
        self.secret_manager = secret_manager
        self._registry = registry
        self._cache = LLMClientCache()
        self._lock = RLock()
        self._current = LLMSet()
        # id(set) -> runs in flight on it.
        self._runs: Dict[int, int] = {}

    # ------------------------------------------------------------ building

    def build(self, agents: Mapping[str, Any], llm: Optional["LLM"] = None) -> LLMSet:
        """A new set from ``agents`` (name -> ``AgentConfig`` or chat model object). Swaps nothing.

        Every client is built now, through the cache, without calling a model.
        An agent whose API key does not resolve gets no client: ``missing``
        says why, and ``get_llm`` raises it.

        Raises:
            ValueError: an agent names an unknown provider or no model.
        """
        configs = {name: agent if agent.name == name else agent.model_copy(update={"name": name})
                   for name, agent in agents.items() if isinstance(agent, AgentConfig)}
        objects = {name: agent for name, agent in agents.items() if name not in configs}
        for agent in configs.values():
            self._validate(agent)
        clients, keys, errors, missing = {}, {}, {}, {}
        for name, agent in configs.items():
            try:
                api_key = self._api_key_for(agent)
            except ValueError as e:
                errors[name], missing[name] = e, str(e)
                continue
            key = ClientKey.of(agent, api_key)
            try:
                clients[name] = self._cache.acquire(key, lambda agent=agent, api_key=api_key:
                                                    self._build_client(agent, api_key=api_key))
            except Exception as e:  # noqa: BLE001 - raised by get_llm, where it was raised before
                errors[name] = e
                continue
            keys[name] = key
        return LLMSet(configs=configs, objects=objects, clients=clients, keys=keys, errors=errors,
                      missing=missing, llm=llm)

    def build_from(self, llm: "LLM") -> LLMSet:
        """A new set from the ``llm`` section. Swaps nothing."""
        return self.build(agents_from_config(llm), llm)

    def derive(self, llm: "LLM") -> "LLMRegistry":
        """A registry for one run, sharing this one's client cache, its current set built from ``llm``.

        Evaluation and trace replay use it (``Runtime.derive``): swapping its
        set leaves this registry's alone, while equal agent settings still
        share one client. It is never closed; this registry owns the cache.
        """
        run = LLMRegistry(self.secret_manager, self._registry)
        run._cache = self._cache
        run._current = run.build_from(llm)
        return run

    def swap(self, llms: LLMSet) -> LLMSet:
        """Makes ``llms`` the current set and returns the old one. Runs in flight keep theirs."""
        with self._lock:
            old, self._current = self._current, llms
        self._retire_if_unused(old)
        return old

    def discard(self, llms: LLMSet) -> None:
        """Gives back a set that was built and never swapped in."""
        self._release_keys(llms)

    def close(self) -> None:
        """Closes every client. The engine is shutting down."""
        self._cache.close()

    # ------------------------------------------------------------ runs

    def begin_run(self) -> RunLLMs:
        """Pins the current set to this run: ``get_llm`` in this context reads it until ``unpin``."""
        with self._lock:
            llms = self._current
            self._runs[id(llms)] = self._runs.get(id(llms), 0) + 1
        return RunLLMs(self, llms, _RUN_LLMS.set(llms))

    def _end_run(self, llms: LLMSet) -> None:
        with self._lock:
            left = self._runs.get(id(llms), 0) - 1
            if left > 0:
                self._runs[id(llms)] = left
                return
            self._runs.pop(id(llms), None)
        self._retire_if_unused(llms)

    def _retire_if_unused(self, llms: LLMSet) -> None:
        with self._lock:
            if llms is self._current or self._runs.get(id(llms)):
                return
        self._release_keys(llms)

    def _release_keys(self, llms: LLMSet) -> None:
        for key in llms.keys.values():
            self._cache.release(key)

    # ------------------------------------------------------------ reading

    @property
    def current(self) -> LLMSet:
        """The set this context reads: its run's, inside a run, otherwise the engine's current one."""
        pinned = _RUN_LLMS.get()
        if pinned is not None:
            return pinned
        with self._lock:
            return self._current

    @property
    def llms(self) -> Dict[str, BaseChatModel]:
        """The clients the current set built, by agent name."""
        return dict(self.current.clients)

    def missing_keys(self) -> Dict[str, str]:
        """Each agent whose API key does not resolve, mapped to the reason. No model is called."""
        return dict(self.current.missing)

    def get_llm(self, name: str) -> BaseChatModel:
        """Returns the client for an agent, from the current set.

        When the caller brought its own keys (:mod:`nl2sql.llm.request_key`)
        the client is built from them, and from the caller's choice of provider
        and model for this step, and cached nowhere, so it belongs to that
        request and to nothing else.

        Args:
            name: Agent name; falls back to the 'default' agent.

        Returns:
            BaseChatModel: That agent's client (``ChatOpenAI``, or
            ``ChatAnthropic`` for the anthropic provider).

        Raises:
            ValueError: If neither the named agent nor a 'default' agent is
                configured, or if the provider needs an API key that cannot be
                resolved.
        """
        llms = self.current
        request = current_request_llms()
        if request and llms.object_for(name) is None:
            config, key = self._for_request(llms.config_for(name), name, request)
            return self._build_client(config, api_key=key)
        return llms.client(name)

    def effective(self, name: str) -> str:
        """``provider/model`` agent ``name`` runs on in this context, the request's choice included."""
        llms = self.current
        request = current_request_llms()
        if request and llms.object_for(name) is None:
            config, _ = self._for_request(llms.config_for(name), name, request)
            return f"{config.provider}/{config.model}"
        return llms.describe(name)

    # ------------------------------------------------------------ the 0.4 entry points

    def register_llms(self, config: Dict[str, AgentConfig]):
        """Sets each agent under its key in ``config`` and keeps the others, as one swap.

        The key is the agent's name (``decomposer``, ``astplanner``, ...). An
        entry's own ``name`` field defaults to 'default', so trusting it made
        every unnamed entry under ``agents`` overwrite, and then be overwritten
        by, the default agent.
        """
        current = self.current
        self.swap(self.build({**current.objects, **current.configs, **config}, current.llm))

    def register_llm(self, agent: AgentConfig):
        """Sets one agent and keeps the others, as one swap.

        Raises:
            ValueError: If the provider is unknown or the model is empty.
        """
        self.register_llms({agent.name: agent})

    def replace_llms(self, config: Dict[str, AgentConfig]):
        """Replaces every agent with those in ``config``, as one swap.

        Unlike ``register_llms`` this also forgets agents the new config no
        longer names, so they fall back to 'default'. Every entry is validated
        before anything changes: an invalid config leaves the old one in place.
        """
        self.swap(self.build(config))

    def _validate(self, agent: AgentConfig) -> None:
        known = self._registry.names("llm") if self._registry is not None else sorted(PROVIDER_PRESETS)
        if agent.provider not in known:
            raise ValueError(
                f"Unsupported LLM provider: {agent.provider}. "
                f"Valid providers are: {', '.join(known)}."
            )
        if not agent.model or not agent.model.strip():
            raise ValueError(
                f"LLM agent '{agent.name}' has no model configured. "
                "Set 'model' for it in the llm section."
            )

    def _provider(self, name: str):
        """The ``LLMProvider`` for ``name``, through the registry when there is one."""
        if self._registry is not None:
            return self._registry.load("llm", name)
        from .providers import LLM_PROVIDERS

        return LLM_PROVIDERS[name]

    def _api_key_for(self, agent: AgentConfig) -> Optional[str]:
        """The resolved key ``agent``'s client is built with, or None for a provider's own business.

        Raises:
            ValueError: the provider needs a key and none resolves.
        """
        preset = PROVIDER_PRESETS.get(agent.provider)
        if preset is not None:
            api_key = self._resolve_api_key(agent, preset)
        elif agent.api_key is not None:
            # A provider registered in code: its key is the config's, or its own business.
            api_key = self.secret_manager.resolve_object(agent.api_key)
        else:
            api_key = None
        if hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()
        return api_key or None


    @staticmethod
    def _for_request(agent: AgentConfig, name: str, request: RequestLLMs):
        """``agent`` as this request asked for it, and the key to build it with.

        A step the caller chose a provider and model for moves there and takes
        that provider's key; the temperature moves with the model, because a
        model that rejects ``temperature: 0`` must be sent none. A step with no
        choice of its own is what it always was: the caller's key, with the
        agent moved to that key's provider when it belongs to another one.

        Raises:
            MissingProviderKey: when the step's provider has no key here.
        """
        from .providers import VERIFIED_MODELS

        if request.base_url:
            # One endpoint for the whole request (the hosted demo's replay
            # server): every step speaks the OpenAI wire to it, whatever it is
            # configured on, and no per-step choice applies. It is asked for
            # the request's model, when it names one: the replay server
            # echoes it, so the run reports the model that recorded it.
            _, _, key = request.resolve(name, agent.provider)
            update = {"provider": "openai", "base_url": request.base_url, "api_key": None}
            if request.model:
                update["model"] = request.model
            return agent.model_copy(update=update), key

        provider, model, key = request.resolve(name, agent.provider)
        if provider is None:
            return LLMRegistry._for_key(agent, key), key
        # A model off the verified list sends no temperature at all, which
        # every model accepts; the caller's choice is checked before it gets here.
        temperature = VERIFIED_MODELS.get(provider, {}).get(model)
        if provider == agent.provider:
            if model == agent.model:
                return agent, key
            # The same provider: only the model and its temperature change, so
            # a configured endpoint (and a test's fake one) stays in force.
            return agent.model_copy(update={"model": model, "temperature": temperature}), key
        return agent.model_copy(update={"provider": provider, "model": model,
                                        "temperature": temperature,
                                        "base_url": None, "api_key": None}), key

    @staticmethod
    def _for_key(agent: AgentConfig, key: str) -> AgentConfig:
        """``agent`` as the key's own provider would run it.

        A key names its provider by its shape, and the configured model belongs
        to the configured provider: a ``gpt-`` model means nothing to Anthropic.
        So a key from another provider moves the agent to that provider's
        default model and endpoint; a key from the configured provider changes
        nothing but the credential, which keeps a pinned model (and a test's
        fake endpoint) in force.
        """
        from .providers import default_model_for, default_temperature_for, provider_for_key

        provider = provider_for_key(key)
        if provider == agent.provider:
            return agent
        return agent.model_copy(update={"provider": provider, "model": default_model_for(provider),
                                        "temperature": default_temperature_for(provider),
                                        "base_url": None, "api_key": None})

    def _build_client(self, agent: AgentConfig, api_key: Optional[str] = None) -> BaseChatModel:
        """Builds the client for one agent through its provider in ``LLM_PROVIDERS``.

        Args:
            agent: Validated configuration for the agent.
            api_key: A key supplied by the caller, which wins over the config
                and the environment and is never stored.

        Returns:
            BaseChatModel: A client pointed at the configured endpoint.

        Raises:
            ValueError: If the wire's optional dependency (the anthropic
                extra) is missing.
        """
        from .providers import LLM_AGENTS, ResolvedAgent
        from .wires import AGENT_KEY, PARSE_RETRIES_KEY, STRUCTURED_OUTPUT_KEY

        if not api_key:
            api_key = self._api_key_for(agent)
        provider = self._provider(agent.provider)
        structured_output = agent.structured_output or provider.structured_output
        headers = {name: self._resolve_secret(value) for name, value in agent.headers.items()}
        client = provider.create(ResolvedAgent(
            name=agent.name, model=agent.model, temperature=agent.temperature,
            api_key=api_key, base_url=agent.base_url, structured_output=structured_output,
            max_tokens=agent.max_tokens, timeout_s=agent.timeout_s, max_retries=agent.max_retries,
            headers=headers,
        ))
        # ``structured()`` reads these off the client, whichever provider built it.
        agent_name = next((name for name, key in LLM_AGENTS.items() if key == agent.name), agent.name)
        client.metadata = {**(client.metadata or {}), STRUCTURED_OUTPUT_KEY: structured_output,
                           PARSE_RETRIES_KEY: agent.parse_retries, AGENT_KEY: agent_name}
        return client

    def _resolve_secret(self, value: Any) -> str:
        """A header value, its ``${provider:key}`` reference resolved."""
        resolved = self.secret_manager.resolve_object(value)
        return resolved.get_secret_value() if hasattr(resolved, "get_secret_value") else str(resolved)

    def _resolve_api_key(self, agent: AgentConfig, preset: ProviderPreset):
        """Resolves the API key for an agent, or explains what is missing.

        Args:
            agent: Configuration for the agent.
            preset: Preset for the agent's provider.

        Returns:
            The resolved key, the provider's placeholder for key-free providers,
            or the value of the provider's environment variable.

        Raises:
            ValueError: If the provider requires a key and none can be found.
        """
        try:
            resolved = self.secret_manager.resolve_object(agent.api_key)
        except ValueError:
            # An unresolvable "${env:...}" reference is the same situation as no
            # key at all, and is reported as such below.
            resolved = None

        if resolved is not None and resolved.get_secret_value():
            return resolved

        if preset.api_key_placeholder:
            return preset.api_key_placeholder

        env_var = self._api_key_env_var(agent, preset)
        from_env = os.environ.get(env_var) if env_var else None
        if from_env:
            return from_env

        raise ValueError(
            f"LLM agent '{agent.name}' uses provider '{agent.provider}', which "
            f"requires an API key, but none could be resolved. Set {env_var} in the "
            "environment or in the .env next to nl2sql.yaml, or give it an api_key "
            "in the llm section of nl2sql.yaml (llm.api_key, or llm.agents.<agent>.api_key)."
        )

    @staticmethod
    def _api_key_env_var(agent: AgentConfig, preset: ProviderPreset) -> Optional[str]:
        """Returns the environment variable the agent's key should come from.

        A config that already says ``${env:SOME_VAR}`` names its own variable;
        anything else falls back to the provider's conventional one.
        """
        raw = agent.api_key.get_secret_value() if agent.api_key else ""
        if raw and raw.startswith("${env:") and raw.endswith("}"):
            return raw[len("${env:") : -1]
        return preset.api_key_env

    def get_llm_config(self, name: str) -> Dict[str, Any]:
        """One agent's settings, without its key; ``default``'s for an agent with none of its own."""
        listed = self.list_llms()
        return listed.get(name) or listed["default"]

    def list_llms(self) -> Dict[str, Dict[str, Any]]:
        """Every agent's settings, without keys or header values. An agent given a model object shows its class."""
        llms = self.current
        listed = {name: {**config.model_dump(exclude={"api_key", "headers"}), "headers": sorted(config.headers)}
                  for name, config in llms.configs.items()}
        listed.update({name: {"name": name, "provider": None, "model": type(model).__name__}
                       for name, model in llms.objects.items()})
        return listed
