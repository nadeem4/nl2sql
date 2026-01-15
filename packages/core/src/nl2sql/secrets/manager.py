from __future__ import annotations

import os
import re
from typing import Optional, Dict, Type, List, Any
from .interfaces import SecretProvider
from .providers.env import EnvironmentSecretProvider
from nl2sql.configs.secrets import SecretProviderConfig
import logging

logger = logging.getLogger(__name__)

class SecretManager:
    """Manages secret resolution using registered providers."""
    
    def __init__(self):
        self._providers: Dict[str, SecretProvider] = {
            "env": EnvironmentSecretProvider()
        }
        self._default_provider = "env"

    def register_provider(self, provider_id: str, provider: SecretProvider) -> None:
        self._providers[provider_id] = provider

    def configure(self, configs: List[SecretProviderConfig]) -> None:
        """Configures providers from a list of configuration objects.
        
        This method implements a Two-Phase Loading strategy:
        1. Bootstrap: It assumes the 'env' provider is already active.
        2. Resolution: It resolves configuration values (like client_secret) that may contain
           secret references (e.g., "${env:VAR}") using the existing providers.
        3. Registration: It instantiates and registers the new providers.
        
        Args:
            configs: A list of SecretProviderConfig objects.
        """
        from .factory import SecretProviderFactory

        for config in configs:
            try:
                if config.type == "env":
                    continue
                
                updates = {}
                for key, value in config.model_dump(exclude={"id", "type"}).items():
                    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                        updates[key] = self.resolve(value)
                
                resolved_config = config.model_copy(update=updates)
                provider = SecretProviderFactory.create(resolved_config)
                    
                if provider:
                    self.register_provider(config.id, provider)
                    logger.info(f"Registered secret provider '{config.id}' (type: {config.type})")
                    
            except Exception as e:
                logger.error(f"Failed to configure secret provider '{config.id}': {e}")
                
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

