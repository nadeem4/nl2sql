"""The base of Wave C's stored and returned models: unknown keys are errors, not ignored."""
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """A model that refuses keys it does not declare, so a typo or a stale stored document fails loudly."""

    model_config = ConfigDict(extra="forbid")
