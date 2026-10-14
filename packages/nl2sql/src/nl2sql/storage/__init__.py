"""The stores: how a ``storage`` sub-block becomes a store object.

The four interfaces are in ``nl2sql_sdk.stores``. The built-ins, ``chroma``,
``sqlite``, ``files`` and ``local``, are declared in the ``nl2sql.stores``
entry point group like any plugin, and found through the registry. See
docs/architecture/phase2.md, section 4.

This module imports no store's library: a store is imported when it is built.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, List

from pydantic import BaseModel, ConfigDict, ValidationError

from nl2sql.config.models import STORE_LABELS, StoreConfig

if TYPE_CHECKING:
    from nl2sql.registry import Registry
    from nl2sql.secrets import SecretManager


class NoKeys(BaseModel):
    """The ``config_model`` of a built-in store that takes no keys besides ``type``."""

    model_config = ConfigDict(extra="forbid")


def build_store(slot: str, value: Any, *, folder: Path, registry: "Registry", secrets: "SecretManager") -> Any:
    """The store for ``slot`` from its ``storage`` sub-block. A store object is used as given.

    For a ``StoreConfig``: loads the store named by ``type``, checks it fills
    ``slot``, resolves the ``${provider:key}`` references in its keys,
    validates them with its ``config_model`` and builds it with
    ``from_config(cfg, folder=folder)``.

    Raises:
        RegistryError: ``type`` is unknown or cannot be loaded.
        ValueError: the store fills another slot (naming the installed stores
            that fill this one), or its keys are not valid.
    """
    if not isinstance(value, StoreConfig):
        return value
    cls = registry.load("store", value.type)
    found = getattr(cls, "slot", None)
    if found != slot:
        what = STORE_LABELS.get(found, "not a store (it has no 'slot')")
        installed = ", ".join(_installed(registry, slot)) or "none"
        raise ValueError(f"'{value.type}' is {what}; {slot} needs {STORE_LABELS[slot]}. Installed: {installed}.")
    block = secrets.resolve_object(dict(value.model_extra or {}))
    try:
        cfg = cls.config_model.model_validate(block)
    except ValidationError as e:
        # Each key and what is wrong with it; never the value, which may be a secret.
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the block'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"The {value.type} store's keys are not valid: {reasons}.") from None
    return cls.from_config(cfg, folder=Path(folder))


def _installed(registry: "Registry", slot: str) -> List[str]:
    """The registered stores that fill ``slot``. Only reached for an error, so loading each is fine."""
    names = []
    for name in registry.names("store"):
        try:
            if getattr(registry.load("store", name), "slot", None) == slot:
                names.append(name)
        except Exception:  # a broken plugin is reported when it is used, not here
            continue
    return names
