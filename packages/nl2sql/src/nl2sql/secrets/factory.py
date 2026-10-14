"""The built-in secret providers, by ``type``, and how one is built.

Each provider implements ``nl2sql_sdk.SecretProvider``. Its module imports
only pydantic at the top, so its ``config_model`` can validate a config block
without the provider's client library installed; ``from_config`` imports the
library and names the extra to install when it is missing.
"""
import importlib

from pydantic import BaseModel

from nl2sql_sdk import SecretProvider

BUILTIN_PROVIDERS = {
    "env": "nl2sql.secrets.providers.env:EnvSecretProvider",
    "aws": "nl2sql.secrets.providers.aws:AwsSecretProvider",
    "azure": "nl2sql.secrets.providers.azure:AzureSecretProvider",
    "hashicorp": "nl2sql.secrets.providers.hashi:HashiCorpSecretProvider",
}


def provider_class(type_name: str) -> type[SecretProvider]:
    """The provider class for ``type_name``.

    Raises:
        ValueError: ``type_name`` is not a built-in provider; the message lists them.
    """
    target = BUILTIN_PROVIDERS.get(type_name)
    if target is None:
        raise ValueError(f"Unknown secret provider type '{type_name}'. "
                         f"Built-in types: {', '.join(sorted(BUILTIN_PROVIDERS))}.")
    module, _, name = target.partition(":")
    return getattr(importlib.import_module(module), name)


def create_provider(type_name: str, options: BaseModel) -> SecretProvider:
    """Build the provider for ``type_name`` from its validated, resolved block.

    Raises:
        ImportError: The provider's client library is not installed; the
            message names the extra to install.
        Exception: Whatever the provider raised while starting.
    """
    return provider_class(type_name).from_config(options)
