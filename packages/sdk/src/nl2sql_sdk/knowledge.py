"""Knowledge: the glossary's models and the ``KnowledgeSource`` interface.

A glossary is what people write about their data, per datasource: table and
column descriptions, synonyms, column roles, units, grains and code values,
joins the database does not declare, business terms, prose rules and example
questions. The built-in ``yaml`` source reads ``glossary.yaml``; a plugin (a
wiki, a data catalog) implements ``KnowledgeSource`` and is installed under the
``nl2sql.knowledge_sources`` entry point group.

Every model is strict (``extra="forbid"``). Every text field is at most
``item_max_chars`` long (500 unless the validation context says otherwise, as
the engine passes ``settings.knowledge.item_max_chars``): a longer one is a
load error naming the item, so nothing is silently cut. ``notes`` is for
people and never sent to a model.

This module imports only pydantic and the standard library. See
docs/architecture/wave_b.md, section 3, and decision D36.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, ClassVar, Dict, List, Literal, Optional, Protocol, Union, runtime_checkable

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

ITEM_MAX_CHARS = 500
"""The longest a glossary text may be when the validation context sets no ``item_max_chars``."""

Role = Literal["identifier", "enum", "money", "quantity", "date", "text"]
Grain = Literal["second", "day", "week", "month", "quarter", "year"]
Cardinality = Literal["one-to-one", "one-to-many", "many-to-one", "many-to-many", "unknown"]

_SLUG = re.compile(r"[a-z0-9][a-z0-9_]*")


def _capped(value: str, info: ValidationInfo) -> str:
    limit = (info.context or {}).get("item_max_chars", ITEM_MAX_CHARS)
    if len(value) > limit:
        raise ValueError(f"is {len(value)} characters long; a glossary text is at most {limit} "
                         f"(settings.knowledge.item_max_chars)")
    return value


Text = Annotated[str, AfterValidator(_capped)]
"""A glossary text: at most ``item_max_chars`` characters."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TableKnowledge(_Model):
    """What people say about one table."""

    description: Optional[Text] = None
    notes: Optional[Text] = None  # for people; never sent to a model


class ColumnKnowledge(_Model):
    """What people say about one column. ``values`` maps a code (a string) to its meaning, on ``role: enum`` only."""

    description: Optional[Text] = None
    notes: Optional[Text] = None
    synonyms: List[Text] = Field(default_factory=list)
    role: Optional[Role] = None
    unit: Optional[Text] = None
    grain: Optional[Grain] = None
    values: Dict[str, Text] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _values_on_an_enum_only(self) -> "ColumnKnowledge":
        if self.values and self.role != "enum":
            raise ValueError("values are written on a column with role: enum only")
        return self


ColumnNames = Union[str, List[str]]
"""``table.column``, or a list of them for a composite key."""


class JoinKnowledge(_Model):
    """A join the database does not declare. ``from_`` is ``from`` in YAML."""

    from_: ColumnNames = Field(alias="from")
    to: ColumnNames
    cardinality: Cardinality = "unknown"
    notes: Optional[Text] = None


class Term(_Model):
    """A business term: what a word means in this datasource, and the columns it is computed from."""

    term: Text = Field(min_length=1)
    synonyms: List[Text] = Field(default_factory=list)
    definition: Optional[Text] = None
    columns: List[str] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)


class Rule(_Model):
    """A business rule in prose (D40), sent when a sub-query reads one of ``tables`` (empty: every sub-query)."""

    id: str
    text: Text = Field(min_length=1)
    tables: List[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG.fullmatch(value):
            raise ValueError(f"'{value}' is not a slug: lower-case letters, digits and underscores")
        return value


class DatasourceKnowledge(_Model):
    """One datasource's knowledge. Table and column names are resolved against the live snapshot when used."""

    tables: Dict[str, TableKnowledge] = Field(default_factory=dict)
    columns: Dict[str, ColumnKnowledge] = Field(default_factory=dict)
    joins: List[JoinKnowledge] = Field(default_factory=list)
    terms: List[Term] = Field(default_factory=list)
    rules: List[Rule] = Field(default_factory=list)
    examples: List[Text] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, rules: List[Rule]) -> List[Rule]:
        ids = [rule.id for rule in rules]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"rule ids are unique in a datasource: {', '.join(duplicates)} is repeated")
        return rules


class Glossary(_Model):
    """A whole glossary: ``version: 1`` and the knowledge per datasource id."""

    version: Literal[1]
    datasources: Dict[str, DatasourceKnowledge] = Field(default_factory=dict)


@runtime_checkable
class KnowledgeSource(Protocol):
    """Where knowledge comes from. A source only reads (D36); the engine writes only ``glossary.yaml``.

    - ``interface_version``: the version of this contract the source was
      written for. It is 1; the engine accepts exactly that.
    - ``config_model``: a pydantic model for the source's keys in the
      ``knowledge`` section apart from ``type``. Set ``extra="forbid"``.
    - ``from_config(cfg, *, folder)``: builds the source from a validated
      ``config_model`` instance. ``folder`` is the folder of ``nl2sql.yaml``.
    - ``load()``: the glossary, read now. Called when the engine is built and
      by ``engine.knowledge.reload()``, never during a run.
    """

    interface_version: ClassVar[int]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_config(cls, cfg: BaseModel, *, folder: Path) -> "KnowledgeSource":
        ...

    def load(self) -> Glossary:
        ...


__all__ = [
    "ITEM_MAX_CHARS",
    "Role",
    "Grain",
    "Cardinality",
    "Text",
    "TableKnowledge",
    "ColumnKnowledge",
    "ColumnNames",
    "JoinKnowledge",
    "Term",
    "Rule",
    "DatasourceKnowledge",
    "Glossary",
    "KnowledgeSource",
]
