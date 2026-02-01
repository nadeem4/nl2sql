"""
Settings API for NL2SQL

Provides functionality for configuration and settings management.
"""

from __future__ import annotations

from typing import Dict, Any

from nl2sql.context import NL2SQLContext
from nl2sql.common.settings import Settings


class SettingsAPI:
    """
    API for configuration and settings management.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
        self._settings: Settings = ctx.config_manager.settings if hasattr(ctx, 'config_manager') else ctx.settings if hasattr(ctx, 'settings') else None
    
    def get_current_settings(self) -> Dict[str, Any]:
        """
        Get the current application settings.
        
        Returns:
            Dictionary of current settings
        """
        if self._settings:
            return self._settings.model_dump()
        else:
            # Fallback to the global settings
            from nl2sql.common.settings import settings
            return settings.model_dump()
    
    def get_setting(self, key: str) -> Any:
        """
        Get a specific setting value.
        
        Args:
            key: Setting key to retrieve
            
        Returns:
            Value of the setting
        """
        settings_dict = self.get_current_settings()
        return settings_dict.get(key)
    
    def validate_configuration(self) -> bool:
        """
        Validate the current configuration.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            # Try to access all settings to validate them
            self.get_current_settings()
            return True
        except Exception:
            return False