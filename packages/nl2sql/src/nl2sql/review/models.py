"""What the review queue returns and raises (docs/architecture/wave_b.md, section 5).

The proposal row itself is the SDK's ``nl2sql_sdk.stores.Proposal`` (D36), so
a third-party state store can keep it.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql_sdk.stores import Proposal


class AcceptReport(BaseModel):
    """What ``engine.proposals.accept`` and ``apply`` did.

    ``accepted``: the proposals now accepted; ``files_written``: each file
    written, once; ``warnings``: such as an earlier knowledge source shadowing
    what was written; ``refused``: proposal id to why it was not accepted (or,
    from ``apply``, rejected); ``rejected``: the proposals ``apply`` rejected.
    """

    accepted: List[Proposal] = Field(default_factory=list)
    rejected: List[Proposal] = Field(default_factory=list)
    files_written: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    refused: Dict[str, str] = Field(default_factory=dict)


class ReviewErrorCode(str, Enum):
    """Why the review queue refused."""

    NOT_AUTHORIZED = "NOT_AUTHORIZED"  # ``by`` holds none of policies.admin_roles
    NOT_WRITABLE = "NOT_WRITABLE"  # no yaml knowledge source, or curated not loaded from its own file
    FILE_CHANGED = "FILE_CHANGED"  # the file changed on disk since it was loaded
    NOT_FOUND = "NOT_FOUND"  # no such proposal, or one the caller's scope cannot see


class ReviewError(Exception):
    """The review queue refused: ``code`` says why and ``fix`` what to do."""

    def __init__(self, code: ReviewErrorCode, message: str, *, fix: Optional[str] = None):
        self.code = code
        self.message = message
        self.fix = fix
        super().__init__(f"{message} {fix}" if fix else message)
