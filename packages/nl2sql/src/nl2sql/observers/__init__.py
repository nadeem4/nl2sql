"""Observers: code that receives an engine's events, built from the ``observers`` section.

The built-ins are ``log`` (``observers/log.py``), ``audit``
(``observers/audit.py``) and ``otel`` (``observers/otel.py``, the ``otel``
extra). A plugin declares its own under the ``nl2sql.observers`` entry point
group. With no section the engine runs ``log`` and ``audit``; ``observers: []``
runs none. Observers fail open: the bus catches what one raises (D9). The
design is docs/architecture/wave_a.md, section 4.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from pydantic import ValidationError

from nl2sql.config.models import ObserverConfig

if TYPE_CHECKING:
    from nl2sql.registry import Registry
    from nl2sql.secrets import SecretManager

# ``observers`` absent from the config (D18).
DEFAULT_OBSERVERS = (ObserverConfig(type="log"), ObserverConfig(type="audit"))


def build_observer(block: ObserverConfig, *, registry: "Registry", secrets: "SecretManager", folder: Path) -> Any:
    """The observer named by ``block.type``, built from the block's other keys.

    Loads it through the registry, resolves the ``${provider:key}``
    references in its keys, validates them with its ``config_model`` and
    builds it with ``from_config``, giving it ``folder`` (``storage.path``).

    Raises:
        RegistryError: ``type`` is unknown, cannot be loaded or needs an extra that is not installed.
        ValueError: its keys are not valid.
    """
    cls = registry.load("observer", block.type)
    keys = secrets.resolve_object(dict(block.model_extra or {}))
    try:
        cfg = cls.config_model.model_validate(keys)
    except ValidationError as e:
        # Each key and what is wrong with it; never the value, which may be a secret.
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the block'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"The {block.type} observer's keys are not valid: {reasons}.") from None
    return cls.from_config(cfg, folder=folder)


def build_observers(entries: Optional[List[Any]], *, registry: "Registry", secrets: "SecretManager",
                    folder: Path, problem: Callable[[str, str], None]) -> List[Any]:
    """Every observer the section lists, in order: a block is built, an object is used as given.

    ``entries`` None means the defaults. A block that cannot be built is
    reported through ``problem(path, message)`` and left out; the caller
    raises one ``ConfigError`` for all of them.
    """
    from nl2sql.registry import RegistryError

    observers: List[Any] = []
    for i, entry in enumerate(DEFAULT_OBSERVERS if entries is None else entries):
        if not isinstance(entry, ObserverConfig):
            observers.append(entry)
            continue
        try:
            observers.append(build_observer(entry, registry=registry, secrets=secrets, folder=folder))
        except RegistryError as e:
            problem(f"observers[{i}].type", str(e))
        except ValueError as e:
            problem(f"observers[{i}]", str(e))
    return observers
