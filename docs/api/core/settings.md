# Settings API (System)

## Purpose
Expose runtime settings and configuration validation.

## Responsibilities
- Return current settings.
- Fetch specific setting keys.
- Validate configuration integrity.

## Key Modules
- `packages/core/src/nl2sql/api/settings_api.py`
- `packages/core/src/nl2sql/common/settings.py`

## Public Surface

### SettingsAPI.get_current_settings

Signature:
`get_current_settings() -> Dict[str, Any]`

Returns:
Settings as a dictionary (`Settings.model_dump()`).

### SettingsAPI.get_setting

Signature:
`get_setting(key: str) -> Any`

Returns:
Value for the given key or `None`.

### SettingsAPI.validate_configuration

Signature:
`validate_configuration() -> bool`

Returns:
`True` if settings can be loaded; `False` otherwise.
