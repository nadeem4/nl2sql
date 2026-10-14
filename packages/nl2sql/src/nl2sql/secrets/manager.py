from __future__ import annotations

from typing import Dict, List, Any
from nl2sql_sdk import SecretProvider
from .providers.env import EnvSecretProvider
from .models import SecretProviderConfig
import logging

logger = logging.getLogger(__name__)

class SecretManager:
    """Manages secret resolution using registered providers."""

    def __init__(self):
        self._providers: Dict[str, SecretProvider] = {
            "env": EnvSecretProvider()
        }
        self._default_provider = "env"
        # Every value a reference resolved to, so a run trace can redact them.
        self._resolved: set = set()

    @property
    def resolved_values(self) -> frozenset:
        """The secret values this manager has handed out so far."""
        return frozenset(self._resolved)

    def register_provider(self, provider_id: str, provider: SecretProvider) -> None:
        self._providers[provider_id] = provider

    def configure(self, configs: List[SecretProviderConfig]) -> None:
        """Configures providers from a list of configuration objects.
        
        Each provider's block was validated by its ``config_model`` when the
        config loaded. For each entry this method:
        1. resolves the references in the block (e.g. ``token: "${env:VAULT_TOKEN}"``)
           with the providers already registered, ``env`` among them;
        2. builds the provider with its ``from_config`` and registers it under ``id``.

        Args:
            configs: A list of SecretProviderConfig objects.

        Raises:
            ValueError: A configured provider could not start (its package is
                missing, a credential reference did not resolve, or the
                provider itself raised). This is fatal on purpose: swallowing
                it only moves the failure to the first ``${id:key}`` lookup,
                where it reads as "Unknown secret provider ID".
        """
        from pydantic import SecretStr

        from nl2sql.registry import Registry

        registry = Registry.default()

        for config in configs:
            if config.type == "env":
                continue

            resolved_values = []
            try:
                updates = {}
                for key in type(config.options).model_fields:
                    value = getattr(config.options, key)
                    # Credential fields are SecretStr; a reference in one is resolved too.
                    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
                    if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
                        resolved = self.resolve(raw)
                        resolved_values.append(resolved)
                        updates[key] = SecretStr(resolved) if isinstance(value, SecretStr) else resolved

                provider = registry.load("secrets", config.type).from_config(
                    config.options.model_copy(update=updates))
            except Exception as e:
                reason = str(e) or type(e).__name__
                for value in resolved_values:
                    if value:
                        reason = reason.replace(value, "***")
                # No cause: the provider's own exception may carry a credential.
                raise ValueError(
                    f"Secret provider '{config.id}' (type '{config.type}') failed to start: {reason} "
                    "Fix or remove this entry in the secrets config."
                ) from None

            self.register_provider(config.id, provider)
            logger.info(f"Registered secret provider '{config.id}' (type: {config.type})")

    def resolve(self, secret_ref: str) -> str:
        """Resolves a secret reference string.

        Format: ${provider_id:key}
        
        Args:
            secret_ref: The reference string to resolve.

        Returns:
            str: The resolved secret value.

        Raises:
            ValueError: If the format is invalid, provider is unknown, or secret
                is not found.
        """
        cleaned_ref = secret_ref.replace('${', '').replace('}', '')
        
        parts = cleaned_ref.split(':', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid secret format '{secret_ref}'. Expected '${{provider_id:key}}'.")
            
        provider_id, key = parts
        provider = self._providers.get(provider_id)
        if not provider:
            raise ValueError(f"Unknown secret provider ID: '{provider_id}'")
            
        val = provider.get_secret(key)
        if val is not None:
            self._resolved.add(str(val))
            return val
        
        raise ValueError(f"Secret not found: {secret_ref}")

    def resolve_object(self, obj: Any) -> Any:
        """Recursively resolves secret references in a generic object.

        Traverses the object structure (Pydantic models, dicts, lists) and resolves
        any string values matching the secret pattern "${...}". Handles SecretStr
        by unwrapping, resolving, and re-wrapping.

        Args:
            obj: The object to resolve secrets in. Can be a Pydantic model,
                dictionary, list, string, or SecretStr.

        Returns:
            Any: A new object with all secret references resolved to their
                actual values.
        """
        from pydantic import BaseModel, SecretStr

        if isinstance(obj, str):
            if obj.startswith("${") and obj.endswith("}"):
                return self.resolve(obj)
            return obj
            
        if isinstance(obj, SecretStr):
            secret_val = obj.get_secret_value()
            if secret_val and secret_val.startswith("${") and secret_val.endswith("}"):
                resolved_val = self.resolve(secret_val)
                return SecretStr(resolved_val)
            return obj
        
        if isinstance(obj, BaseModel):
            updates = {}
            for field_name in type(obj).model_fields.keys():
                val = getattr(obj, field_name)
                resolved = self.resolve_object(val)
                if resolved != val:
                    updates[field_name] = resolved
            
            if updates:
                return obj.model_copy(update=updates)
            return obj
            
        if isinstance(obj, list):
            return [self.resolve_object(item) for item in obj]
            
        if isinstance(obj, dict):
             return {k: self.resolve_object(v) for k, v in obj.items()}
             
        return obj

