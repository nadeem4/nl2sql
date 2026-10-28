"""The registry: turns a name in the config (``type: postgres``) into code.

A ``Registry`` is a value passed to the engine, not a global, so two engines
or two tests never see each other's registrations. ``Registry.default()``
lists the installed entry points and imports none of them; a name is imported,
and checked, when ``load`` is asked for it. See
docs/architecture/configuration_v2.md, sections 5 and 6.

This module imports only the standard library, so ``Registry.default()`` stays
cheap: the import-time rule in docs/architecture/invariants.md.
"""
from __future__ import annotations

import difflib
import functools
import importlib.util
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

Kind = Literal["adapter", "llm", "embeddings", "secrets", "store", "policy_check", "observer", "hook", "scorer",
               "knowledge_source", "chat_tool"]

# The entry point group each kind is installed under.
GROUPS: Dict[str, str] = {
    "adapter": "nl2sql.adapters",
    "llm": "nl2sql.llms",
    "embeddings": "nl2sql.embeddings",
    "secrets": "nl2sql.secrets",
    "store": "nl2sql.stores",
    "policy_check": "nl2sql.policy_checks",
    "observer": "nl2sql.observers",
    "hook": "nl2sql.hooks",
    "scorer": "nl2sql.scorers",
    "knowledge_source": "nl2sql.knowledge_sources",
    "chat_tool": "nl2sql.chat_tools",
}

# The interface version this engine was built for, per kind. Anything else is
# a load error: there are no minor versions and no window for old ones.
INTERFACE_VERSIONS: Dict[str, int] = {"adapter": 2, "llm": 2, "embeddings": 2, "secrets": 1, "store": 4,
                                   "policy_check": 1, "observer": 1, "hook": 1, "scorer": 1,
                                   "knowledge_source": 1, "chat_tool": 1}

# How a kind reads in a message.
_LABELS: Dict[str, str] = {
    "adapter": "adapter",
    "llm": "LLM provider",
    "embeddings": "embeddings provider",
    "secrets": "secret provider",
    "store": "store",
    "policy_check": "policy check",
    "observer": "observer",
    "hook": "hook",
    "scorer": "scorer",
    "knowledge_source": "knowledge source",
    "chat_tool": "chat tool",
}

# The attribute naming the modules a kind needs before it can be built.
_REQUIRES: Dict[str, str] = {"adapter": "driver_modules", "llm": "requires", "embeddings": "requires",
                             "observer": "requires"}

# One installed entry point: (name, "module:attr", the package that declares it).
_Entry = Tuple[str, str, str]


class RegistryError(ValueError):
    """A name could not be registered or loaded. The message says why and how to fix it."""


@functools.cache
def _scan() -> Dict[str, Tuple[_Entry, ...]]:
    """Every installed entry point in the registry's groups, as plain data.

    Cached: it is the one part of ``Registry.default()`` that is worth caching,
    and it holds names only, never an imported object.
    """
    found: Dict[str, Tuple[_Entry, ...]] = {}
    for group in GROUPS.values():
        found[group] = tuple(
            (ep.name, ep.value, ep.dist.name if ep.dist else "an unknown package")
            for ep in entry_points(group=group)
        )
    return found


def _installed(module: str) -> bool:
    """True when ``module`` can be imported. Finds it without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # a dotted name whose parent package is missing
        return False


def missing_modules(kind: str, target: Any) -> List[str]:
    """The modules ``target``, a loaded adapter or provider, needs and are not installed."""
    attribute = _REQUIRES.get(kind)
    return [module for module in (getattr(target, attribute, ()) if attribute else ()) if not _installed(module)]


class Registry:
    """Name -> code, per kind: adapters, LLM providers, embeddings providers, secret providers, stores, policy
    checks, observers, hooks, scorers, knowledge sources and chat tools."""

    def __init__(self) -> None:
        self._installed: Dict[str, Dict[str, List[_Entry]]] = {kind: {} for kind in GROUPS}
        self._added: Dict[str, Dict[str, Any]] = {kind: {} for kind in GROUPS}
        self._loaded: Dict[Tuple[str, str], Any] = {}

    @classmethod
    def default(cls) -> "Registry":
        """The built-ins and every installed plugin. Lists names; imports nothing."""
        registry = cls()
        scan = _scan()
        for kind, group in GROUPS.items():
            for entry in scan.get(group, ()):
                found = registry._installed[kind].setdefault(entry[0], [])
                # An editable install next to a checkout can list one entry twice.
                if entry not in found:
                    found.append(entry)
        return registry

    @classmethod
    def empty(cls) -> "Registry":
        """Nothing registered: for tests and allow-lists."""
        return cls()

    # ------------------------------------------------------------ registering

    def add_adapter(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("adapter", name, cls, replace)

    def add_llm(self, name: str, provider: Any, *, replace: bool = False) -> None:
        self._add("llm", name, provider, replace)

    def add_embeddings(self, name: str, provider: Any, *, replace: bool = False) -> None:
        self._add("embeddings", name, provider, replace)

    def add_secrets(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("secrets", name, cls, replace)

    def add_store(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("store", name, cls, replace)

    def add_policy_check(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("policy_check", name, cls, replace)

    def add_observer(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("observer", name, cls, replace)

    def add_hook(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("hook", name, cls, replace)

    def add_scorer(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("scorer", name, cls, replace)

    def add_knowledge_source(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("knowledge_source", name, cls, replace)

    def add_chat_tool(self, name: str, cls: type, *, replace: bool = False) -> None:
        self._add("chat_tool", name, cls, replace)

    def adapter(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("adapter", name)

    def llm(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("llm", name)

    def embeddings(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("embeddings", name)

    def secrets(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("secrets", name)

    def store(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("store", name)

    def policy_check(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("policy_check", name)

    def observer(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("observer", name)

    def hook(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("hook", name)

    def scorer(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("scorer", name)

    def knowledge_source(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("knowledge_source", name)

    def chat_tool(self, name: str) -> Callable[[Any], Any]:
        return self._decorator("chat_tool", name)

    def _decorator(self, kind: str, name: str) -> Callable[[Any], Any]:
        def register(target: Any) -> Any:
            self._add(kind, name, target, False)
            return target
        return register

    def _add(self, kind: str, name: str, target: Any, replace: bool) -> None:
        if not replace and name in self.names(kind):  # type: ignore[arg-type]
            raise RegistryError(f"The {_LABELS[kind]} '{name}' is already registered. "
                                "Pass replace=True to replace it.")
        self._added[kind][name] = target
        self._loaded.pop((kind, name), None)

    # ------------------------------------------------------------ reading

    def names(self, kind: Kind) -> List[str]:
        """Every name registered for ``kind``, sorted."""
        return sorted(set(self._installed[kind]) | set(self._added[kind]))

    def load(self, kind: Kind, name: str, *, require_installed: bool = True) -> Any:
        """The code behind ``name``, imported and checked.

        ``require_installed=False`` skips the check that the modules it needs
        are installed (``missing_modules`` lists them), to describe an adapter
        whose driver is not installed yet: its module imports without it.

        Raises:
            RegistryError: the name is unknown (with the closest name), two
                installed packages declare it (naming both), it implements
                another interface version (naming its package), or a module
                it needs is not installed (with its install hint).
        """
        key = (kind, name)
        if key in self._loaded:
            return self._loaded[key]
        if name in self._added[kind]:
            target = self._added[kind][name]
            origin = f"The {_LABELS[kind]} '{name}' registered in code"
        elif name in self._installed[kind]:
            target, origin = self._import(kind, name)
        else:
            raise RegistryError(self._unknown(kind, name))
        self._check_version(kind, target, origin)
        if not require_installed:
            return target
        self._check_installed(kind, name, target)
        self._loaded[key] = target
        return target

    def _import(self, kind: str, name: str) -> Tuple[Any, str]:
        entries = self._installed[kind][name]
        if len(entries) > 1:
            packages = " and ".join(sorted({package for _, _, package in entries}))
            raise RegistryError(f"Two installed packages declare the {_LABELS[kind]} '{name}': "
                                f"{packages}. Uninstall one of them.")
        _, value, package = entries[0]
        target = EntryPoint(name=name, value=value, group=GROUPS[kind]).load()
        return target, package

    @staticmethod
    def _check_version(kind: str, target: Any, origin: str) -> None:
        version = getattr(target, "interface_version", 0)
        needed = INTERFACE_VERSIONS[kind]
        if version != needed:
            raise RegistryError(f"{origin} implements {_LABELS[kind]} interface {version}; "
                                f"this engine needs {needed}.")

    @staticmethod
    def _check_installed(kind: str, name: str, target: Any) -> None:
        missing = missing_modules(kind, target)
        if missing:
            hint = getattr(target, "install_hint", "")
            raise RegistryError(f"The {_LABELS[kind]} '{name}' needs {', '.join(missing)}, which is not "
                                f"installed." + (f" Install it: {hint}" if hint else ""))

    def _unknown(self, kind: str, name: str) -> str:
        known = self.names(kind)  # type: ignore[arg-type]
        close: Optional[List[str]] = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        message = f"Unknown {_LABELS[kind]} '{name}'."
        if close:
            message += f" Did you mean '{close[0]}'?"
        return message + (f" Registered: {', '.join(known)}." if known else " None is registered.")
