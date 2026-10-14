"""How a ``policies.check`` block becomes a policy check object."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from nl2sql.config.models import PolicyCheckConfig
    from nl2sql.registry import Registry
    from nl2sql.secrets import SecretManager


def build_check(block: "PolicyCheckConfig", *, registry: "Registry", secrets: "SecretManager") -> Any:
    """The policy check named by ``block.type``, built from the block's other keys.

    Loads the check through the registry, resolves the ``${provider:key}``
    references in its keys, validates them with its ``config_model`` and
    builds it with ``from_config``.

    Raises:
        RegistryError: ``type`` is unknown or cannot be loaded.
        ValueError: its keys are not valid.
    """
    cls = registry.load("policy_check", block.type)
    keys = secrets.resolve_object(dict(block.model_extra or {}))
    try:
        cfg = cls.config_model.model_validate(keys)
    except ValidationError as e:
        # Each key and what is wrong with it; never the value, which may be a secret.
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the block'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"The {block.type} policy check's keys are not valid: {reasons}.") from None
    return cls.from_config(cfg)
