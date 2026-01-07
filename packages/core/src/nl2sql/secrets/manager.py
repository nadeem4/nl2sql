from __future__ import annotations

import os
import re
from typing import Optional, Dict, Type
from .interfaces import SecretProvider
from .providers.env import EnvironmentSecretProvider

class SecretManager:
    """Manages secret resolution using registered providers."""
    
    def __init__(self):
        self._providers: Dict[str, SecretProvider] = {
            "env": EnvironmentSecretProvider()
        }
        self._default_provider = "env"

    def register_provider(self, scheme: str, provider: SecretProvider) -> None:
        self._providers[scheme] = provider

    def resolve(self, secret_ref: str) -> str:
        """
        Resolves a secret reference string.
        Format: ${scheme:key}
        Examples:
            '${env:DB_PASS}' -> fetches DB_PASS from env
            '${azure:my-secret}' -> fetches my-secret from azure keyvault
            '${gcp:my-secret}' -> fetches my-secret from gcp secret manager
            '${aws:my-secret}' -> fetches my-secret from aws secret manager
            '${hvac:my-secret}' -> fetches my-secret from hvac
        """
        cleaned_ref = secret_ref.replace('${', '').replace('}', '')
        
        parts = cleaned_ref.split(':', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid secret format '{secret_ref}'. Expected '${{scheme:key}}'.")
            
        scheme, key = parts
        provider = self._providers.get(scheme)
        if not provider:
            raise ValueError(f"Unknown secret provider scheme: '{scheme}'")
            
        val = provider.get_secret(key)
        if val is not None:
            return val
        
        raise ValueError(f"Secret not found: {secret_ref}")

secret_manager = SecretManager()
