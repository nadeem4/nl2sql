from typing import ClassVar, Optional, Protocol, Self, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class SecretProvider(Protocol):
    """Contract for a secret provider: where ``${id:key}`` references are read from.

    A provider is named by ``type`` in an entry of the secrets config, for
    example ``type: hashicorp``. The engine validates the rest of that entry
    (every key except ``id`` and ``type``) with ``config_model``, resolves any
    ``${env:...}`` in it, and builds the provider with ``from_config``.

    ``config_model`` should set ``extra="forbid"`` so a misspelt key is an
    error, and hold credentials as ``SecretStr`` so they never print. A
    provider reads its settings from that block only, never from the
    environment.

    ``interface_version`` is the version of this contract the provider was
    written for. The engine accepts exactly the version it was built for.
    """

    interface_version: ClassVar[int]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_config(cls, cfg: BaseModel) -> Self:
        """Build the provider from a validated ``config_model`` instance.

        Raise ``ImportError`` naming the package to install when the
        provider's client library is missing.
        """
        ...

    def get_secret(self, key: str) -> Optional[str]:
        """Return the secret stored under ``key``, or None if there is none."""
        ...
