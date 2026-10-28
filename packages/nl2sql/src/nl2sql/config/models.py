"""The typed config: ``Config`` and one model per section of ``nl2sql.yaml``.

Every model is strict (``extra="forbid"``) and frozen. Field names are the YAML
keys. ``load_config`` builds a ``Config`` from the files; building one in code
gives exactly the same object. See docs/architecture/configuration_v2.md,
section 2.
"""
from __future__ import annotations

import re
import types
import typing
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, NonNegativeFloat, NonNegativeInt, PositiveFloat,
                      PositiveInt, PrivateAttr, field_validator, model_validator)
from pydantic.fields import FieldInfo
from typing_extensions import Annotated

from nl2sql_sdk import ConnectionModel, IndexScope, Limits

from nl2sql.config.curated import CuratedDatasource
from nl2sql.policies.patterns import parse_pattern


class KeepsReferences:
    """Marks a value whose ``${provider:key}`` references ``load_config`` keeps.

    A secret is resolved when the component that uses it is built, so the
    config, logs and errors only ever hold the reference.
    """


KEEPS_REFERENCES = KeepsReferences()

Secret = Annotated[str, KEEPS_REFERENCES]
"""A secret value, or a ``${provider:key}`` reference to one, kept unresolved."""

# "datasources[0].type" -> ("datasources", "0", "type"): a section, an item and the rest.
_SECTION_PATH = re.compile(r"([A-Za-z_]+)(?:\[(\d+)\])?\.?(.*)")

_UNIONS = (Union, getattr(types, "UnionType", Union))

# A datasource's ``connection`` and a secret provider's settings are free-form,
# so a secret among them is recognised by its key.
SECRET_KEY_HINTS = ("password", "secret", "token", "api_key")

MASK = "**********"


def is_secret_key(key: Any) -> bool:
    """Whether a free-form key (``password``, ``client_secret``, ``token``, ...) holds a secret."""
    return isinstance(key, str) and any(hint in key.lower() for hint in SECRET_KEY_HINTS)


def _annotation_keeps_references(annotation: Any) -> bool:
    if typing.get_origin(annotation) is Annotated:
        args = typing.get_args(annotation)
        return KEEPS_REFERENCES in args[1:] or _annotation_keeps_references(args[0])
    if typing.get_origin(annotation) in _UNIONS:
        return any(_annotation_keeps_references(a) for a in typing.get_args(annotation))
    if typing.get_origin(annotation) is dict:
        return _annotation_keeps_references(typing.get_args(annotation)[1])
    return False


def _values_are_secrets(field: FieldInfo) -> bool:
    """Whether ``field`` is a mapping of ``Secret`` values (``headers``): every value masks, whatever its key."""
    annotation = field.annotation
    while typing.get_origin(annotation) in _UNIONS:
        annotation = next(a for a in typing.get_args(annotation) if a is not type(None))
    return typing.get_origin(annotation) is dict and _annotation_keeps_references(annotation)


def keeps_references(field: FieldInfo) -> bool:
    """Whether ``field`` is a ``Secret`` or a ``connection``: it keeps its references."""
    return KEEPS_REFERENCES in field.metadata or _annotation_keeps_references(field.annotation)


def masked(value: Any) -> Any:
    """``value`` as it may print: a literal secret is masked, a ``${provider:key}`` reference is not."""
    if isinstance(value, str) and value and not value.startswith("${"):
        return MASK
    return value


def _masked_by_key(values: Dict[Any, Any]) -> Dict[Any, Any]:
    return {key: masked(value) if is_secret_key(key) else value for key, value in values.items()}

AgentName = Literal[
    "curated_matcher",
    "datasource_resolver",
    "decomposer",
    "ast_planner",
    "refiner",
    "answer_synthesizer",
    "profiler",  # drafting table and column descriptions, only with profile(draft=True) (Wave B, D44)
    "eval_seeder",  # seeding a draft eval suite (V3)
    "conversation",  # the model-led turn of engine.conversations (Wave C, D101)
]


class _Model(BaseModel):
    """Strict, frozen, and a secret never prints.

    The value stays a plain ``str`` the engine reads as it is; only ``repr``
    and ``str`` mask it. A validation error never echoes its input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True,
                              hide_input_in_errors=True)

    def __repr_args__(self):
        fields = type(self).model_fields
        for key, value in super().__repr_args__():
            if key in fields and keeps_references(fields[key]):
                if isinstance(value, dict):
                    value = ({k: masked(v) for k, v in value.items()} if _values_are_secrets(fields[key])
                             else _masked_by_key(value))
                else:
                    value = masked(value)
            yield key, value


# ---------------------------------------------------------------- datasources

class Datasource(_Model):
    """One database the engine can query."""

    id: str = Field(min_length=1)
    type: Optional[str] = None
    description: Optional[str] = None
    # Validated against the adapter's ``connection_model`` when the adapter is
    # built. A dict keeps every reference until then; a model instance is used
    # as given.
    connection: Union[Annotated[Dict[str, Any], KEEPS_REFERENCES], ConnectionModel] = Field(
        default_factory=dict, union_mode="left_to_right")
    limits: Limits = Field(default_factory=Limits)
    index: IndexScope = Field(default_factory=IndexScope)
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

    # Never negative: a negative rate would drive a run's spend down and lift settings.pipeline.max_cost_usd.
    input: NonNegativeFloat
    output: NonNegativeFloat
    cached_input: Optional[NonNegativeFloat] = None
    cache_write: Optional[NonNegativeFloat] = None


# How a structured answer is asked for: a forced tool call, a JSON schema the
# server decodes against, or plain JSON mode with the schema in the prompt.
StructuredOutput = Literal["tool_calling", "json_schema", "json_mode"]


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
    structured_output: Optional[StructuredOutput] = None  # None: the provider's
    parse_retries: NonNegativeInt = 1  # retries of a malformed structured answer
    max_tokens: Optional[PositiveInt] = None  # None: the provider's
    timeout_s: Optional[PositiveFloat] = None  # per request; None: the client library's
    max_retries: Optional[NonNegativeInt] = None  # network and rate-limit retries; None: the client library's
    headers: Dict[str, Secret] = Field(default_factory=dict)  # sent with every request; values never print


class LLM(_Model):
    """The default LLM, with per-agent overrides and model prices."""

    provider: Optional[str] = None
    model: Any  # a model name, or a chat model object (code only)
    temperature: Optional[float] = 0.0
    api_key: Optional[Secret] = None  # None: the provider's standard variable
    base_url: Optional[str] = None
    structured_output: Optional[StructuredOutput] = None
    parse_retries: NonNegativeInt = 1
    max_tokens: Optional[PositiveInt] = None
    timeout_s: Optional[PositiveFloat] = None
    max_retries: Optional[NonNegativeInt] = None
    headers: Dict[str, Secret] = Field(default_factory=dict)
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
    base_url: Optional[str] = None  # openai and openai_compatible; an error for local
    dimension: Optional[PositiveInt] = None  # None: the provider's, else probed with one call
    instance: Any = None  # an embeddings object (code only)

    @model_validator(mode="after")
    def _provider_or_instance(self) -> "Embeddings":
        if self.provider is None and self.instance is None:
            raise ValueError("provider is required, unless an embeddings instance is given")
        return self


# ---------------------------------------------------------------- policies

CostAction = Literal["allow", "confirm", "refuse"]


class CostRule(_Model):
    """The cost guard (``policies.cost``): a limit on the rows a query is estimated to read.

    ``over_limit``, and ``unknown`` (no estimate: not declared, failed, or no
    rows), each ``allow``, ``confirm`` (a person approves, through Wave A's
    approval) or ``refuse``. ``max_estimated_rows: null`` is no limit. On a
    role, the fields it sets replace the top-level ones for that role. Enforced
    by the built-in ``policy.cost`` hook from C2 (docs/architecture/wave_b.md,
    section 9).
    """

    max_estimated_rows: Optional[PositiveInt] = None
    over_limit: CostAction = "confirm"
    unknown: CostAction = "allow"


class Role(_Model):
    """What one role may read: allow and deny patterns over ``datasource.schema.table``.

    A pattern is ``*``, ``datasource`` or ``datasource.schema.table``, each part
    a glob; matching ignores case. Deny beats allow within the role. ``cost``
    replaces, for this role, the fields of ``policies.cost`` it sets.
    """

    description: str
    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)
    cost: Optional[CostRule] = None

    @field_validator("allow", "deny")
    @classmethod
    def _patterns(cls, value: List[str]) -> List[str]:
        for pattern in value:
            parse_pattern(pattern)
        return value


class Policies(_Model):
    """Who may read what. ``{allow_all: true}`` is a complete section.

    ``check`` is an optional policy check: a ``PolicyCheckConfig``
    (``{type: ..., <its keys>}``) naming a registered one, or a check object,
    used as given. What it grants is intersected with the roles; with
    ``allow_all`` the check alone decides.
    """

    allow_all: bool = False
    default_role: Optional[str] = None
    admin_roles: List[str] = Field(default_factory=lambda: ["admin"])
    refusal_names_tables: bool = False
    roles: Dict[str, Role] = Field(default_factory=dict)
    check: Any = None  # a PolicyCheckConfig or a check object
    # The cost guard; absent, no estimate is ever asked for. Valid beside allow_all, which opens tables, not cost.
    cost: Optional[CostRule] = None

    @field_validator("check", mode="before")
    @classmethod
    def _check(cls, value: Any) -> Any:
        if value is None or isinstance(value, PolicyCheckConfig):
            return value
        if isinstance(value, dict):
            return PolicyCheckConfig(**value)
        if isinstance(value, (str, list)) or not callable(getattr(value, "grant", None)):
            raise ValueError("check takes a mapping with a 'type' key, such as {type: corp_iam}, "
                             "or a policy check object (one with a grant method)")
        return value

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

class _PluginBlock(BaseModel):
    """A block a plugin validates itself: ``type`` names the plugin, the other keys are its own.

    This model allows the other keys and keeps their references; the plugin's
    ``config_model`` validates them when it is built.
    """

    model_config = ConfigDict(extra="allow", frozen=True, hide_input_in_errors=True)

    type: str = Field(min_length=1)

    def __repr_args__(self):
        # A credential (``token``, ``api_key``) written as a literal never
        # prints; a reference, and every other setting, does.
        for key, value in super().__repr_args__():
            yield key, masked(value) if is_secret_key(key) else value


class PolicyCheckConfig(_PluginBlock):
    """A policy check, by name: ``PolicyCheckConfig(type="corp_iam", url=...)``. The check's ``config_model`` checks the other keys."""


class StoreConfig(_PluginBlock):
    """One store, by name: ``StoreConfig(type="files")``. The store's ``config_model`` checks the other keys."""


STORE_SLOTS = ("index", "state", "traces", "results")

# How each slot reads in a message: "'chroma' is an index store".
STORE_LABELS = {"index": "an index store", "state": "a state store",
                "traces": "a trace store", "results": "a results store"}

# The store each slot uses when the config does not name one.
BUILT_IN_STORES = {"index": "chroma", "state": "sqlite", "traces": "files", "results": "local"}


class Storage(_Model):
    """Where the engine keeps its data, and which store keeps each part of it.

    In a file, a relative ``path`` is relative to ``nl2sql.yaml``; in code, to
    the working directory. Each of ``index``, ``state``, ``traces`` and
    ``results`` is a ``StoreConfig`` (``{type: ..., <its keys>}``) or a store
    object, used as given. Left out, each is its built-in store under ``path``:
    ``chroma`` in ``index/``, ``sqlite`` in ``state.db``, ``files`` in
    ``traces/`` and ``local`` in ``results/``.
    """

    path: Path
    index: Union[StoreConfig, Any] = Field(default_factory=lambda: StoreConfig(type=BUILT_IN_STORES["index"]))
    state: Union[StoreConfig, Any] = Field(default_factory=lambda: StoreConfig(type=BUILT_IN_STORES["state"]))
    traces: Union[StoreConfig, Any] = Field(default_factory=lambda: StoreConfig(type=BUILT_IN_STORES["traces"]))
    results: Union[StoreConfig, Any] = Field(default_factory=lambda: StoreConfig(type=BUILT_IN_STORES["results"]))

    @field_validator(*STORE_SLOTS, mode="before")
    @classmethod
    def _store(cls, value: Any, info) -> Any:
        slot = info.field_name
        if isinstance(value, dict):
            return StoreConfig(**value)
        if isinstance(value, StoreConfig):
            return value
        if isinstance(value, (str, list)) or value is None:
            raise ValueError(f"{slot} takes a mapping with a 'type' key, such as "
                             f"{{type: {BUILT_IN_STORES[slot]}}}, or a store object")
        found = getattr(value, "slot", None)
        if found != slot:
            what = STORE_LABELS.get(found, "not a store (it has no 'slot')")
            raise ValueError(f"{type(value).__name__} is {what}; {slot} needs {STORE_LABELS[slot]}")
        return value


# ---------------------------------------------------------------- secrets

class SecretProviderConfig(_PluginBlock):
    """One secret provider. ``type`` names it; the provider checks the other keys."""

    id: str = Field(min_length=1)


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
    # Per-question caps (enforced from E3): model calls, parse retries
    # included, and dollars. None turns a cap off.
    max_llm_calls: Optional[PositiveInt] = 60
    max_cost_usd: Optional[PositiveFloat] = None
    # The answer sanity checks (read from S1).
    sanity_checks: bool = True


class RetrievalSettings(_Model):
    full_snapshot_max_tables: int = Field(15, ge=0)
    # Read by the schema retriever and the resolver from R1: they were
    # TABLE_K, PLANNING_K and MAX_DECOMPOSER_TABLES.
    table_k: PositiveInt = 8
    planning_k: PositiveInt = 12
    decomposer_tables: PositiveInt = 30


class RoutingSettings(_Model):
    """Cross-database routing (read from R1 and R2)."""

    candidates: PositiveInt = 3  # datasources shown to the answerability check (D28)
    max_datasources: PositiveInt = 3  # datasources one question may use
    max_sub_queries: PositiveInt = 6  # sub-queries one question may have
    floor_ratio: float = Field(0.5, ge=0.0, le=1.0)  # drop a candidate less similar than this share of the best


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


class CuratedSettings(_Model):
    match_top_k: PositiveInt = 5
    match_floor: float = Field(0.6, ge=0.0, le=1.0)


class FeedbackSettings(_Model):
    enabled: bool = True


class KnowledgeSettings(_Model):
    """How much knowledge reaches a prompt (read from K1 and K2)."""

    item_max_chars: PositiveInt = 500  # one glossary text; longer is a load error
    block_max_chars: PositiveInt = 4000  # the whole [BUSINESS_CONTEXT] block; cut by score, and traced
    top_k: PositiveInt = 8  # matched terms kept per sub-query
    match_floor: float = Field(0.6, ge=0.0, le=1.0)  # a term entry's similarity to match (K3)


class ClarifySettings(_Model):
    """Clarify instead of guess (read from L1)."""

    enabled: bool = True


class ProfilerSettings(_Model):
    """The schema profiler's bounds (read from P1 and P2)."""

    sample_rows: PositiveInt = 1000  # distinct values one overlap probe reads
    max_probes: PositiveInt = 200  # overlap probes per datasource
    time_budget_s: PositiveFloat = 60  # per datasource; no probe starts after it
    min_overlap: float = Field(0.9, ge=0.0, le=1.0)
    enum_max_distinct: PositiveInt = 50
    max_cost_usd: PositiveFloat = 0.50  # drafting, per profile call


class ReviewSettings(_Model):
    """The review queue (read from Q1)."""

    max_age_days: PositiveInt = 365  # decided proposals are purged after this


class ChartSettings(_Model):
    """How a result becomes a chart (read from K1; docs/architecture/wave_c.md, section 5)."""

    max_points: PositiveInt = 1000  # rows charted; a longer line becomes a table
    max_categories: PositiveInt = 20  # bars
    max_series: PositiveInt = 5  # lines


class Settings(_Model):
    """Tuning knobs. Every field has a default, so the section is optional."""

    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = "default_tenant"
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    results: ResultSettings = Field(default_factory=ResultSettings)
    traces: TraceSettings = Field(default_factory=TraceSettings)
    # ``schema`` would shadow a BaseModel method, so the attribute is ``schema_``.
    schema_: SchemaSettings = Field(default_factory=SchemaSettings, alias="schema")
    curated: CuratedSettings = Field(default_factory=CuratedSettings)
    feedback: FeedbackSettings = Field(default_factory=FeedbackSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    clarify: ClarifySettings = Field(default_factory=ClarifySettings)
    profiler: ProfilerSettings = Field(default_factory=ProfilerSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    charts: ChartSettings = Field(default_factory=ChartSettings)


# ---------------------------------------------------------------- observers, hooks, evals

class ObserverConfig(_PluginBlock):
    """One observer, by name: ``ObserverConfig(type="log")``. The observer's ``config_model`` checks the other keys.

    The engine builds its observers from the ``observers`` section from E2.
    """


HookPoint = Literal["run.before", "datasources.after", "plan.after", "execute.before", "execute.after",
                    "answer.after"]


class HookConfig(_PluginBlock):
    """One hook, by name: ``HookConfig(type="corp_sql_review", points=["execute.before"])``.

    ``points`` is a subset of the hook's own points (all of them when None);
    ``on_error: open`` lets the run go on when the hook raises (hooks fail
    closed by default); ``approver_roles`` may approve its
    ``needs_approval`` (``policies.admin_roles`` when None). The hook's
    ``config_model`` checks the other keys. The engine builds and runs its
    hooks from H1.
    """

    points: Optional[List[HookPoint]] = None
    on_error: Literal["closed", "open"] = "closed"
    approver_roles: Optional[List[str]] = None


class KnowledgeSourceConfig(_PluginBlock):
    """One knowledge source, by name: ``KnowledgeSourceConfig(type="yaml", path="glossary.yaml")``.

    The source's ``config_model`` checks the other keys. The engine builds and
    loads its knowledge sources from K1 (docs/architecture/wave_b.md, section 3).
    """


_EXAMPLE = {"an observer": "log", "a hook": "corp_sql_review", "a knowledge source": "yaml"}


def _entry(block: type, kind: str, method: str):
    """A list item that is a ``{type: ...}`` block or an object (one with ``method``), used as given."""

    def check(value: Any) -> Any:
        if isinstance(value, dict):
            return block(**value)
        if isinstance(value, block) or (value is not None and not isinstance(value, (str, list, int, float, bool))
                                        and hasattr(value, method)):
            return value
        raise ValueError(f"each item is {kind}: a mapping with a 'type' key, such as "
                         f"{{type: {_EXAMPLE[kind]}}}, or an object with {method}")

    return check


ObserverEntry = Annotated[Union[ObserverConfig, Any],
                          BeforeValidator(_entry(ObserverConfig, "an observer", "on_event"))]
HookEntry = Annotated[Union[HookConfig, Any], BeforeValidator(_entry(HookConfig, "a hook", "__call__"))]
KnowledgeEntry = Annotated[Union[KnowledgeSourceConfig, Any],
                           BeforeValidator(_entry(KnowledgeSourceConfig, "a knowledge source", "load"))]

# The built-in scorers (from V1): the default list.
DEFAULT_SCORERS = ("result_match", "outcome", "faithfulness", "retrieval_recall", "routing")


class EvalGate(_Model):
    """The regression gate (from V2): regressions, accuracy and cost against a baseline."""

    max_regressions: NonNegativeInt = 2  # mode full: cases that passed in the baseline and fail now
    alpha: float = Field(0.05, gt=0.0, lt=1.0)  # mode full: McNemar's exact two-sided p, when more were lost
    max_cost_increase: float = Field(0.20, ge=0.0)  # more cost per case, as a share


class EvalsConfig(_Model):
    """The ``evals`` section: the suites, the scorers and the gate. ``engine.evals`` reads it from V1.

    A relative suite path is relative to the folder of ``nl2sql.yaml``. A
    suite file is data, read when a run asks for it, not when the engine
    starts.
    """

    suites: List[Path] = Field(default_factory=list)
    scorers: List[str] = Field(default_factory=lambda: list(DEFAULT_SCORERS))
    curated: bool = True  # D12: curated answers on in mode full
    max_cost_usd: Optional[PositiveFloat] = None  # across a run in mode full; required for it
    gate: EvalGate = Field(default_factory=EvalGate)


# ---------------------------------------------------------------- Wave C sections (docs/architecture/wave_c.md)

class Conversations(_Model):
    """The ``conversations`` section: how a model-led turn is bounded and kept (read from C1)."""

    context_turns: PositiveInt = 3  # turns the model sees
    max_tool_calls: PositiveInt = 12  # tool calls per turn; the model then makes one last call, tools off
    max_turns: PositiveInt = 50
    max_age_days: Optional[PositiveInt] = 30  # None keeps them; also bounds draft dashboards


class Dashboards(_Model):
    """The ``dashboards`` section (read from D1 to D3)."""

    max_tiles: PositiveInt = 10  # the starter dashboard's size
    min_refresh_minutes: PositiveInt = 15
    schedule_identity_max_age_days: PositiveInt = 30


class McpHttp(_Model):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    path: str = "/mcp"
    allowed_hosts: List[str] = Field(default_factory=lambda: [
        "127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"])  # Host headers accepted
    allowed_origins: List[str] = Field(default_factory=list)  # browser Origins accepted

    @field_validator("path")
    @classmethod
    def _rooted(cls, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("must start with /")
        return path


class Mcp(_Model):
    """The ``mcp`` section: ``nl2sql mcp --http`` (read from M2)."""

    http: McpHttp = Field(default_factory=McpHttp)
    auth: Literal["api_keys", "none"] = "api_keys"  # none: loopback only


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
    # None (no section) means the default observers, log and audit; [] means none.
    observers: Optional[List[ObserverEntry]] = None
    hooks: List[HookEntry] = Field(default_factory=list)  # run in this order
    evals: EvalsConfig = Field(default_factory=EvalsConfig)
    # The knowledge sources, in precedence order; [] (no section) means no knowledge.
    knowledge: List[KnowledgeEntry] = Field(default_factory=list)
    conversations: Conversations = Field(default_factory=Conversations)
    # The chat tools by registered name; None (no section) means the five built-ins, [] means none (ask stays).
    chat_tools: Optional[List[str]] = None
    dashboards: Dashboards = Field(default_factory=Dashboards)
    mcp: Mcp = Field(default_factory=Mcp)

    # The folder of the nl2sql.yaml this config was loaded from, set by
    # load_config and kept by every copy; None when it was built in code.
    _folder: Optional[Path] = PrivateAttr(default=None)
    # Where load_config read each section: ("inline", None), ("file", label)
    # or, for a datasources folder, ("folder", [label per datasource]).
    _sources: Dict[str, Tuple[str, Any]] = PrivateAttr(default_factory=dict)
    # The folder ``datasources`` was read from (None when it is inline or the config was built in code) and the
    # SHA-256 of each file in it when it was loaded, by file name. ``engine.datasources.save`` checks and updates
    # them; every copy of this config shares the one dict.
    _datasources_folder: Optional[Path] = PrivateAttr(default=None)
    _file_hashes: Dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def datasources_folder(self) -> Optional[Path]:
        """The folder the datasources were read from, one ``<id>.yaml`` each; None when ``datasources`` is inline in
        ``nl2sql.yaml`` or the config was built in code."""
        return self._datasources_folder

    def datasource_file_hash(self, name: str) -> Optional[str]:
        """The SHA-256 of ``datasources/<name>`` when it was loaded or last saved; None when it was not there."""
        return self._file_hashes.get(name)

    def record_datasource_file(self, name: str, sha256: str) -> None:
        """Remember the hash of ``datasources/<name>`` as the engine last saw it."""
        self._file_hashes[name] = sha256

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

    @field_validator("chat_tools")
    @classmethod
    def _unique_tools(cls, names: Optional[List[str]]) -> Optional[List[str]]:
        duplicates = sorted({n for n in names or [] if (names or []).count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate chat tools: {duplicates}")
        return names

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
