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
        Format: ${scheme:key} or ${key} (uses default provider).
        Examples:
            '${env:DB_PASS}' -> fetches DB_PASS from env
            '${DB_PASS}' -> fetches DB_PASS from default (env)
            'literal_value' -> returns 'literal_value'
        """
        # Match pattern ${scheme:key} or ${key}
        # Updated to support lowercase, slashes, dashes in keys
        pattern = re.compile(r'\$\{(?:([a-z]+):)?([a-zA-Z0-9_./-]+)(?::-(.*?))?\}')
        
        def replace(match):
            scheme = match.group(1) or self._default_provider
            key = match.group(2)
            default_val = match.group(3)
            
            provider = self._providers.get(scheme)
            if not provider:
                raise ValueError(f"Unknown secret provider scheme: '{scheme}'")
                
            val = provider.get_secret(key)
            if val is not None:
                return val
            
            if default_val is not None:
                return default_val
                
            raise ValueError(f"Secret not found: {secret_ref}")

        if pattern.fullmatch(secret_ref):
            return pattern.sub(replace, secret_ref)
            
        # Also support embedding ${var} inside a string? 
        # For strict security, maybe we only want full string replacement for passwords?
        # But for connection strings like "host=${HOST}", we need partial.
        return pattern.sub(replace, secret_ref)

# Global instance
secret_manager = SecretManager()
