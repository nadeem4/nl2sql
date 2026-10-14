from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .factory import provider_class


class SecretProviderConfig(BaseModel):
    """One entry of ``providers`` in the secrets config.

    ``id`` and ``type`` belong to every entry. The other keys are the
    provider's own settings, validated by its ``config_model`` and held in
    ``options``, so an unknown or misspelt key fails when the config loads.

    Example:
        ``SecretProviderConfig(id="vault", type="hashicorp", url="https://vault:8200",
        token="${env:VAULT_TOKEN}")``
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Used as the scheme in secret references (e.g. ${id:key}).")
    type: str = Field(..., description="The provider: env, aws, azure or hashicorp.")
    options: BaseModel = Field(..., description="The provider's settings, as its config_model.")

    @model_validator(mode="before")
    @classmethod
    def _validate_the_provider_block(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "options" in data:
            return data
        block = dict(data)
        head = {"id": block.pop("id", None), "type": block.pop("type", None)}
        if not isinstance(head["type"], str):
            return {**head, "options": None}
        provider = provider_class(head["type"])
        try:
            options = provider.config_model.model_validate(block)
        except ValidationError as e:
            raise ValueError(f"Invalid settings for secret provider type '{head['type']}': {e}") from None
        return {**head, "options": options}
