"""``engine.llm``: the model each agent runs on, and replacing them at runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Union

from nl2sql.runtime import Runtime, _config_agent_names

if TYPE_CHECKING:
    from nl2sql.policies import UserContext
    from nl2sql.config import LLM
    from nl2sql.llm.llm_set import LLMChange


class LLMAPI:
    """``engine.llm``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def list(self) -> Dict[str, Dict[str, Any]]:
        """Each agent, by its name in the ``llm`` section, with the model it runs on. Never a key.

        ``default`` first, then every agent: ``provider``, ``model``,
        ``temperature`` and ``inherited`` (True when the agent sets nothing
        of its own and runs on ``default``). An agent given a model object
        shows its class as ``model`` and no provider.
        """
        listed = self._ctx.llm_registry.list_llms()
        default = _entry(listed.get("default") or {})
        out = {"default": {**default, "inherited": False}}
        for wire_name, name in _config_agent_names().items():
            if name == "default":
                continue
            own = listed.get(wire_name)
            out[name] = {**(_entry(own) if own else default), "inherited": own is None}
        return out

    def get(self, agent: str) -> Dict[str, Any]:
        """One agent's entry from :meth:`list` (``default`` included).

        Raises:
            ValueError: no agent has that name.
        """
        listed = self.list()
        if agent not in listed:
            raise ValueError(f"Unknown agent '{agent}'. The agents are: {', '.join(listed)}.")
        return listed[agent]

    def providers(self) -> List[Dict[str, Any]]:
        """Each LLM provider this engine can use: ``name``, ``label``, ``key_variable``, ``key_present``, ``models``
        and ``key_shape``.

        The built-ins whose client library is installed, and any provider a
        plugin or ``Registry.add_llm`` registered. ``key_variable`` is the
        environment variable the provider reads its key from (None for one that
        needs no key, such as ``ollama``); ``key_present`` says whether it is
        set. ``models`` lists the verified models (empty when none are known) and ``key_shape`` gives the
        ``pattern`` a key matches, its ``min_length`` and the ``prefix`` of the provider's keys (or None), for a
        form to check a key before sending it. Never the key.
        """
        from nl2sql.llm.providers import describe_providers

        return describe_providers(self._ctx.registry)

    def prices(self) -> Dict[str, Any]:
        """The prices every model call is costed with: the dated table with ``llm.prices`` applied.

        ``checked_on`` is the date the table was last checked against the
        providers' pages; ``models`` maps each model name to its ``input``,
        ``cached_input``, ``cache_write`` and ``output`` rates (USD per million
        tokens; a config row's cache rates may be None, see
        :mod:`nl2sql.llm.prices`), ``verified``, ``source`` (``table`` or
        ``config``) and ``citation`` (where a table row's numbers came from).
        A model not listed is not priced: its calls' cost is null.
        """
        from nl2sql.llm.prices import effective_prices

        return effective_prices(self._ctx.config.llm.prices)

    def replace(self, llm: Union["LLM", Mapping[str, Any]], *, by: Optional["UserContext"] = None,
                via: str = "python") -> "LLMChange":
        """Replace the whole ``llm`` section at once: validated, atomic, logged. Never calls a model.

        ``llm`` is an ``LLM``, or the ``llm`` section as written in
        ``nl2sql.yaml`` (a mapping, such as a request body). Either is
        validated as ``llm.yaml`` is, and every agent's API key must resolve;
        an invalid one raises ``ConfigError`` and changes nothing. Runs already in flight finish on the old LLM. The change is
        logged (one INFO line) and audited, naming ``by`` (or ``via``, the
        entry point) and each agent's old and new provider and model, never a
        key. It is not written to any file.
        """
        return self._ctx.replace_llm(llm, by=by, via=via)


def _entry(listed: Dict[str, Any]) -> Dict[str, Any]:
    return {key: listed.get(key) for key in ("provider", "model", "temperature")}
