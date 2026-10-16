"""The review file: the queue's export and import format (docs/architecture/wave_b.md, section 5, D41).

``nl2sql review export`` writes the open proposals a caller may see; a reviewer
sets each item's ``decision`` (``accept``, ``reject``, or empty to leave it
proposed), may edit its ``change`` and give a ``reason``, in a pull request;
``nl2sql review apply`` reads it back. Only ``id``, ``decision``, ``reason`` and
``change`` are read back; the rest is there for the reviewer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nl2sql_sdk.stores import Proposal

VERSION = 1
HEADER = (
    "# A review file: written by `nl2sql review export`, read by `nl2sql review apply`.\n"
    "# Set each item's decision to accept or reject (leave it empty to keep it proposed); you may edit its\n"
    "# change, and give a reason. apply accepts each `accept` with its change and rejects each `reject`.\n"
)


class ReviewItem(BaseModel):
    """One proposal in a review file."""

    model_config = ConfigDict(extra="ignore")

    id: str
    decision: Optional[Literal["accept", "reject"]] = None
    reason: Optional[str] = None
    change: Optional[Dict[str, Any]] = None


class ReviewFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    proposals: List[ReviewItem] = Field(default_factory=list)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)


def render(proposals: Sequence[Proposal]) -> str:
    """The review file of ``proposals``, every decision empty."""
    items = [{"id": p.id, "decision": None, "reason": None, "kind": p.kind, "datasource_id": p.datasource_id,
              "target": p.target, "change": p.change, "evidence": p.evidence, "origin": p.origin}
             for p in proposals]
    return HEADER + yaml.dump({"version": VERSION, "proposals": items}, Dumper=_Dumper, sort_keys=False,
                              allow_unicode=True, width=100)


def read(path: Path) -> ReviewFile:
    """The review file at ``path``.

    Raises:
        ValueError: it cannot be read, is not YAML, or is not a review file.
    """
    path = Path(path)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"{path.as_posix()} cannot be read: {e.strerror or e}.") from None
    except yaml.YAMLError as e:
        raise ValueError(f"{path.as_posix()} is not valid YAML: {e}") from None
    try:
        return ReviewFile.model_validate(document)
    except ValidationError as e:
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the file'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"{path.as_posix()} is not a review file: {reasons}.") from None
