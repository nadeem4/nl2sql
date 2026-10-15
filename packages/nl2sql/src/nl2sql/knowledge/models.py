"""The knowledge shapes the engine's tracks share (docs/architecture/wave_b.md, D59).

``KnowledgeUsed`` is what one sub-query's prompt was sent: the schema
retriever fills it (K2), the explanation reads it (X1) and ``knowledge.used``
publishes it. ``KnowledgeSourceInfo`` and ``DatasourceKnowledgeView`` are what
``engine.knowledge.sources`` and ``show`` return (K1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql_sdk.knowledge import DatasourceKnowledge

Origin = Literal["knowledge", "database", "derived"]
"""Where an item came from (D38): a knowledge source, the database's own comment, or a fact the snapshot proves."""


class TermMatch(BaseModel):
    """A glossary term sent with a sub-query: what it means, where it came from and how well it matched."""

    model_config = ConfigDict(extra="forbid")

    term: str
    definition: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)
    source: str  # the knowledge source's type, such as "yaml"
    score: float  # 1.0 for the term's name, 0.9 for a synonym, the similarity for an index match


class RuleUse(BaseModel):
    """A business rule sent with a sub-query."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    tables: List[str] = Field(default_factory=list)
    source: str


class KnowledgeUsed(BaseModel):
    """The knowledge one sub-query's prompt carried in ``[BUSINESS_CONTEXT]``, and what the block's cap dropped."""

    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    digest: str  # the datasource's knowledge digest the run was pinned to
    terms: List[TermMatch] = Field(default_factory=list)
    rules: List[RuleUse] = Field(default_factory=list)
    dropped: List[str] = Field(default_factory=list)  # term names and rule ids cut by block_max_chars


class KnowledgeSourceInfo(BaseModel):
    """One knowledge source as ``engine.knowledge.sources`` reports it."""

    type: str
    counts: Dict[str, Dict[str, int]] = Field(default_factory=dict)  # per datasource the caller reaches, per kind
    sha256: Optional[str] = None  # of what it loaded, where it reads a file
    loaded_at: Optional[datetime] = None
    error: Optional[str] = None  # the last reload's error; the previous knowledge stayed live


class DatasourceKnowledgeView(BaseModel):
    """One datasource's merged knowledge as the engine uses it, what the caller may see of it.

    ``origins`` maps an item (``tables.Invoice.description``,
    ``columns.Invoice.Total.role``) to where it came from, and ``sources`` to
    the knowledge source that set it; derived roles are in ``knowledge`` with
    origin ``derived``. ``problems`` are the names that do not resolve
    against the live snapshot, and joins whose types cannot be compared.
    """

    datasource_id: str
    digest: Optional[str] = None
    knowledge: DatasourceKnowledge = Field(default_factory=DatasourceKnowledge)
    origins: Dict[str, Origin] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)
    problems: List[str] = Field(default_factory=list)
