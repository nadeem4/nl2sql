"""The typed config: ``Config`` and one model per section of ``nl2sql.yaml``.

Every model is strict (``extra="forbid"``) and frozen. Field names are the YAML
keys. ``load_config`` builds a ``Config`` from the files; building one in code
gives exactly the same object. See docs/architecture/configuration_v2.md,
section 2.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, PrivateAttr, field_validator, model_validator
from typing_extensions import Annotated

from nl2sql.config.curated import CuratedDatasource


class KeepsReferences:
    """Marks a value whose ``${provider:key}`` references ``load_config`` keeps.

    A secret is resolved when the component that uses it is built, so the
    config, logs and errors only ever hold the reference.
    """


KEEPS_REFERENCES = KeepsReferences()

Secret = Annotated[str, KEEPS_REFERENCES]

# "datasources[0].type" -> ("datasources", "0", "type"): a section, an item and the rest.
_SECTION_PATH = re.compile(r"([A-Za-z_]+)(?:\[(\d+)\])?\.?(.*)")
"""A secret value, or a ``${provider:key}`` reference to one, kept unresolved."""

AgentName = Literal[
    "curated_matcher",
    "datasource_resolver",
    "decomposer",
    "ast_planner",
    "refiner",
    "answer_synthesizer",
    "indexing_enrichment",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


# ---------------------------------------------------------------- datasources

class Limits(_Model):
    """Per-datasource limits. Each one is set here and nowhere else."""

    row_limit: Optional[PositiveInt] = None
    timeout_ms: Optional[PositiveInt] = None
    max_bytes: Optional[PositiveInt] = None


class Datasource(_Model):
    """One database the engine can query."""

    id: str = Field(min_length=1)
    type: Optional[str] = None
    description: Optional[str] = None
    # Validated by the adapter. Every value keeps its references.
    connection: Annotated[Dict[str, Any], KEEPS_REFERENCES] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    preferred_tables: List[str] = Field(default_factory=list)
    sample_questions: List[str] = Field(default_factory=list)
    # Routing hint written by people. Domains generated at indexing are separate
    # and stored with the index.
    domains: List[str] = Field(default_factory=list)
    required: bool = False
    # A built adapter, instead of ``type`` (code only).
    adapter: Any = None

    @field_validator("preferred_tables")
    @classmethod
    def _no_blank_preferred_tables(cls, value: List[str]) -> List[str]:
        names = [name.strip() for name in value]
        if any(not name for name in names):
            raise ValueError("preferred_tables holds a blank name")
        return names

    @model_validator(mode="after")
    def _type_or_adapter(self) -> "Datasource":
        if self.type is None and self.adapter is None:
            raise ValueError("type is required, unless an adapter object is given")
        return self


# ---------------------------------------------------------------- llm

def _is_data(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, list, dict))


class Price(_Model):
    """Prices per million tokens for one model."""

    input: float
    output: float
    cached_input: Optional[float] = None
    cache_write: Optional[float] = None


class AgentLLM(_Model):
    """One agent's override. It takes each field it sets and inherits the rest.

    Inheritance follows ``model_fields_set``, so ``temperature: null`` still
    means "send none".
    """

    provider: Optional[str] = None
    model: Any = None  # a model name, or a chat model object (code only)
    temperature: Optional[float] = None
    api_key: Optional[Secret] = None
    base_url: Optional[str] = None


class LLM(_Model):
    """The default LLM, with per-agent overrides and model prices."""

    provider: Optional[str] = None
    model: Any  # a model name, or a chat model object (code only)
    temperature: Optional[float] = 0.0
    api_key: Optional[Secret] = None  # None: the provider's standard variable
    base_url: Optional[str] = None
    agents: Dict[AgentName, AgentLLM] = Field(default_factory=dict)
    prices: Dict[str, Price] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _provider_for_a_model_name(self) -> "LLM":
        if _is_data(self.model) or self.model == "":
            raise ValueError("model must be a model name or a chat model object")
        if isinstance(self.model, str) and not self.provider:
            raise ValueError("provider is required when model is a name")
        return self


# ---------------------------------------------------------------- embeddings

class Embeddings(_Model):
    """The embeddings provider the index is built with."""

    provider: Optional[str] = None
    model: Optional[str] = None  # None: the provider's default
    api_key: Optional[Secret] = None  # None: the provider's standard variable
    instance: Any = None  # an embeddings object (code only)

    @model_validator(mode="after")
    def _provider_or_instance(self) -> "Embeddings":
        if self.provider is None and self.instance is None:
            raise ValueError("provider is required, unless an embeddings instance is given")
        return self


# ---------------------------------------------------------------- policies

class Role(_Model):
    """What one role may read."""

    description: str
    allowed_datasources: List[str] = Field(default_factory=list)
    allowed_tables: List[str] = Field(default_factory=list)

    @field_validator("allowed_tables")
    @classmethod
    def _namespaced(cls, value: List[str]) -> List[str]:
        """``*``, ``datasource.*``, ``datasource.schema.*``, ``datasource.schema.table``
        or ``datasource.table`` (that table in the datasource's default schema)."""
        for table in value:
            if table == "*":
                continue
            parts = table.split(".")
            if len(parts) not in (2, 3) or any(not p for p in parts) or "*" in parts[:-1]:
                raise ValueError(
                    f"Invalid table '{table}'. Use 'datasource.*', 'datasource.schema.*', "
                    "'datasource.schema.table' or 'datasource.table'."
                )
        return value


class Policies(_Model):
    """Who may read what. ``{allow_all: true}`` is a complete section."""

    allow_all: bool = False
    default_role: Optional[str] = None
    admin_roles: List[str] = Field(default_factory=lambda: ["admin"])
    refusal_names_tables: bool = False
    roles: Dict[str, Role] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _allow_all_stands_alone(self) -> "Policies":
        if self.allow_all and (self.default_role is not None or self.roles):
            raise ValueError("allow_all cannot be combined with default_role or roles")
        if self.default_role is not None and self.default_role not in self.roles:
            raise ValueError(f"default_role '{self.default_role}' is not one of the roles")
        return self


def _allow_all(cls) -> Policies:
    """Every caller may read everything (local and dev use)."""
    return cls(allow_all=True)


# The design names both the ``allow_all`` field and the ``Policies.allow_all()``
# shorthand. Set after the class is built, the classmethod answers on the class,
# while an instance's field value (held in its ``__dict__``) answers on the
# instance.
Policies.allow_all = classmethod(_allow_all)  # type: ignore[assignment,method-assign]


# ---------------------------------------------------------------- storage

class Storage(_Model):
    """Where the engine keeps its data: ``<path>/index/``, ``state.db``, ``traces/``, ``results/``.

    In a file, a relative ``path`` is relative to ``nl2sql.yaml``; in code, to
    the working directory.
    """

    path: Path


# ---------------------------------------------------------------- secrets

class SecretProviderConfig(BaseModel):
    """One secret provider. ``type`` names it; the provider checks the other keys.

    The other keys are the provider's own settings, so this model allows them
    and keeps their references; the provider's ``config_model`` validates them
    when it is built.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)

    def __repr_args__(self):
        # A credential written as a literal must never print: only id, type
        # and a reference show.
        for key, value in super().__repr_args__():
            if key not in ("id", "type") and isinstance(value, str) and not value.startswith("${"):
                value = "**********"
            yield key, value


class Secrets(_Model):
    """Secret providers that ``${<id>:key}`` references name. ``env`` is built in."""

    providers: List[SecretProviderConfig] = Field(default_factory=list)

    @field_validator("providers")
    @classmethod
    def _ids(cls, providers: List[SecretProviderConfig]) -> List[SecretProviderConfig]:
        ids = [p.id for p in providers]
        if "env" in ids:
            raise ValueError("the secret provider id 'env' is reserved for the built-in provider")
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate secret provider ids: {duplicates}")
        return providers


# ---------------------------------------------------------------- curated

class Curated(_Model):
    """Verified queries and metrics per datasource. Present means curated answers are on."""

    datasources: Dict[str, CuratedDatasource] = Field(default_factory=dict)


# ---------------------------------------------------------------- settings

class RetrySettings(_Model):
    max: int = Field(3, ge=0)
    base_delay_s: float = Field(1.0, ge=0)
    max_delay_s: float = Field(10.0, ge=0)
    jitter_s: float = Field(0.5, ge=0)


class PipelineSettings(_Model):
    timeout_s: PositiveInt = 60
    plan_cache: bool = True
    strict_columns: bool = True
    today: Optional[date] = None  # pin the run date
    retries: RetrySettings = Field(default_factory=RetrySettings)


class RetrievalSettings(_Model):
    full_snapshot_max_tables: int = Field(15, ge=0)


class ResultSettings(_Model):
    row_limit: PositiveInt = 100_000
    fetch_batch_size: PositiveInt = 10_000
    preview_rows: int = Field(20, ge=0)


class TraceSettings(_Model):
    mode: Literal["off", "on_failure", "always"] = "on_failure"
    sample_rows: int = Field(50, ge=0)
    max_field_chars: PositiveInt = 20000


class SchemaSettings(_Model):
    max_versions: PositiveInt = 3
    version_mismatch: Literal["warn", "fail", "ignore"] = "warn"


class CuratedSettings(_Model):
    match_top_k: PositiveInt = 5
    match_floor: float = Field(0.6, ge=0.0, le=1.0)


class FeedbackSettings(_Model):
    enabled: bool = True


class ObservabilitySettings(_Model):
    exporter: Literal["none", "console", "otlp"] = "none"
    otlp_endpoint: Optional[str] = None


class Settings(_Model):
    """Tuning knobs. Every field has a default, so the section is optional."""

    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = "default_tenant"
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    results: ResultSettings = Field(default_factory=ResultSettings)
    traces: TraceSettings = Field(default_factory=TraceSettings)
    # ``schema`` would shadow a BaseModel method, so the attribute is ``schema_``.
    schema_: SchemaSettings = Field(default_factory=SchemaSettings, alias="schema")
    curated: CuratedSettings = Field(default_factory=CuratedSettings)
    feedback: FeedbackSettings = Field(default_factory=FeedbackSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


# ---------------------------------------------------------------- api

class Api(_Model):
    """``nl2sql-api``'s own settings. The engine validates them and never reads them."""

    role_header: Optional[str] = None
    role: Optional[str] = None
    trust_body_role: bool = False
    cors_origins: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------- Config

class Config(_Model):
    """The whole config. ``curated`` absent (``None``) means curated answers are off."""

    version: Literal[2] = 2
    datasources: List[Datasource]
    llm: LLM
    embeddings: Embeddings
    policies: Policies
    storage: Storage
    curated: Optional[Curated] = None
    secrets: Secrets = Field(default_factory=Secrets)
    settings: Settings = Field(default_factory=Settings)
    api: Api = Field(default_factory=Api)

    # The folder of the nl2sql.yaml this config was loaded from, set by
    # load_config and kept by every copy; None when it was built in code.
    _folder: Optional[Path] = PrivateAttr(default=None)
    # Where load_config read each section: ("inline", None), ("file", label)
    # or, for a datasources folder, ("folder", [label per datasource]).
    _sources: Dict[str, Tuple[str, Any]] = PrivateAttr(default_factory=dict)

    @property
    def folder(self) -> Optional[Path]:
        """The folder of the ``nl2sql.yaml`` this config was loaded from; None when built in code.

        A relative file path in a datasource's ``connection`` (a SQLite or
        DuckDB file) is read from here, as ``storage.path`` is.
        """
        return self._folder

    def locate(self, path: str) -> Tuple[str, str]:
        """The file a problem at ``path`` is in, and the path inside that file.

        ``path`` is a path in the whole config, ``datasources[0].type``. When
        ``datasources`` was read from ``datasources/``, that is
        ``("datasources/sales.yaml", "type")``. A section written inline in
        ``nl2sql.yaml``, and a config built in code, give
        ``("nl2sql.yaml", path)``.
        """
        match = _SECTION_PATH.fullmatch(path)
        if match is None:
            return "nl2sql.yaml", path
        section, index, rest = match.groups()
        kind, label = self._sources.get(section, ("inline", None))
        if kind == "file":
            return label, path[len(section):].lstrip(".")
        if kind == "folder" and index is not None and int(index) < len(label):
            return label[int(index)], rest
        return "nl2sql.yaml", path

    def __eq__(self, other: Any) -> bool:
        # Where a config was loaded from is not part of it: equal sections are an equal config.
        return type(other) is type(self) and self.__dict__ == other.__dict__

    @field_validator("datasources")
    @classmethod
    def _unique_ids(cls, datasources: List[Datasource]) -> List[Datasource]:
        ids = [d.id for d in datasources]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate datasource ids: {duplicates}")
        return datasources

    def with_datasource(self, datasource: Datasource) -> "Config":
        """A copy of this config with ``datasource`` added. Its id must be new."""
        if any(d.id == datasource.id for d in self.datasources):
            raise ValueError(f"a datasource with id '{datasource.id}' is already configured")
        return self.model_copy(update={"datasources": [*self.datasources, datasource]})

    def without_datasource(self, datasource_id: str) -> "Config":
        """A copy of this config without the datasource ``datasource_id``, which must be configured."""
        if not any(d.id == datasource_id for d in self.datasources):
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self.model_copy(update={"datasources": [d for d in self.datasources if d.id != datasource_id]})
