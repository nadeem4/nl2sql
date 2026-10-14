"""The policy check contract: who is asking (``UserContext``), what a check grants (``Grant``), and ``PolicyCheck``."""

from typing import ClassVar, List, Optional, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class UserContext(BaseModel):
    """Who is asking: the caller's id, tenant and roles."""

    user_id: Optional[str] = Field(default=None, description="Unique identifier for the user.")
    tenant_id: Optional[str] = Field(default=None, description="Organization/Tenant identifier.")
    roles: List[str] = Field(default_factory=list, description="List of assigned roles.")
    model_config = ConfigDict(extra="ignore")


class Grant(BaseModel):
    """What a check lets one caller read in one datasource.

    The patterns are the same as a role's in ``policies.yaml``: ``*``,
    ``datasource`` or ``datasource.schema.table``, each part a glob, matched
    regardless of case. Deny beats allow.
    """

    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)


@runtime_checkable
class PolicyCheck(Protocol):
    """Contract for a policy check: an outside service that decides what a caller may read.

    A check is named by ``type`` in the ``check`` block of the policies
    config, for example ``type: corp_iam``. The engine validates the rest of
    that block with ``config_model``, resolves any ``${env:...}`` in it, and
    builds the check with ``from_config``. ``config_model`` should set
    ``extra="forbid"`` so a misspelt key is an error.

    The engine asks ``grant`` once per caller and datasource per run, and
    keeps no answer across runs. The ``Grant`` is intersected with the YAML
    roles: a table is readable only if both allow it, so neither can widen
    the other. Any exception from ``grant`` denies the datasource for the run.
    The check owns its timeouts (an HTTP client timeout, say).

    ``interface_version`` is the version of this contract the check was
    written for. The engine accepts exactly the version it was built for.
    """

    interface_version: ClassVar[int]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_config(cls, cfg: BaseModel) -> Self:
        """Build the check from a validated ``config_model`` instance."""
        ...

    def grant(self, user: UserContext, datasource_id: str) -> Grant:
        """What ``user`` may read in ``datasource_id``."""
        ...
