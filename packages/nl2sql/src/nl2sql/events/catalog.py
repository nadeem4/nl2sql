"""The event catalog: every event the engine publishes, with a pydantic model for its ``data``.

``EventBus.publish`` refuses a name that is not here and validates ``data``
against its model; ``engine.events.catalog()`` returns the list, so an
observer author and the docs read the same thing. The design is
docs/architecture/wave_a.md, section 3, "The catalog".

Events never carry result rows, sample values, keys or header values (D15).
The question and the answer text are carried and may contain values from the
data. Each model forbids extra keys, so nothing rides along unlisted.

Run events have the run's ``trace_id``; engine events have none, except
where a run caused them. Each event names, in its description, who publishes
it; most publishers arrive with the Wave A PR named there.

Each entry also says who else reads it, so each list has one definition and a
later event joins it by its own entry, not by an edit elsewhere:

- ``stream``: the caller's own stream (``stream_query``) carries it: the
  caller-safe allow-list, D17 (``STREAM_EVENTS``). Operator data (a policy
  denial names a table, a hook decision its reasons, a model call its cost)
  stays off it and reaches an operator through ``engine.events.subscribe``.
- ``audit``: the ``audit`` observer writes it (``AUDIT_EVENTS``, D19), and
  ``audit_only``, when set, narrows it to the values named per data field
  (``hook.decision``: only ``deny``, ``needs_approval``, ``approved`` and
  ``error``).
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.config.models import HookPoint

# How a run ended. ``needs_clarification`` is reserved for Wave B's
# clarification result: nothing produces it in Wave A.
RunStatus = Literal["success", "plan_only", "error", "needs_approval", "stopped", "needs_clarification"]


class EventData(BaseModel):
    """The base of every event's data: strict keys, JSON-ready values."""

    model_config = ConfigDict(extra="forbid")


class UsageTotalsData(EventData):
    """A run's model usage, summed: the shape of ``QuestionUsage.total``."""

    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    cost: Optional[float] = None  # None when any call ran on a model with no price


class ResultSummaryData(EventData):
    """One saved result of a run: what it is, never its rows (``ResultSummary``)."""

    node_id: str
    columns: List[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


# ---------------------------------------------------------------- run events

class RunStarted(EventData):
    question: str
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    datasource_id: Optional[str] = None
    execute: bool = True


class RunFinished(EventData):
    """A run's summary, never rows. ``status`` is None when nothing ran and nothing failed."""

    status: Optional[RunStatus] = None
    duration_s: float
    usage: UsageTotalsData = Field(default_factory=UsageTotalsData)
    error_codes: List[str] = Field(default_factory=list)
    source: Literal["verified", "generated"] = "generated"
    datasources: List[str] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)  # datasource.schema.table
    result_manifest: List[ResultSummaryData] = Field(default_factory=list)
    user_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)


class RunFailed(EventData):
    message: str
    error_code: str
    user_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)


class NodeStarted(EventData):
    node: str
    sub_query_id: Optional[str] = None


class NodeFinished(EventData):
    node: str
    sub_query_id: Optional[str] = None
    seconds: float
    ok: bool = True


class AnswerDelta(EventData):
    field: Literal["summary", "content"]
    text: str


class ResultManifestData(EventData):
    results: List[ResultSummaryData] = Field(default_factory=list)


class LLMCall(EventData):
    node: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    cost: Optional[float] = None
    usage_reported: bool = True
    error: Optional[str] = None  # redacted


class LLMParseRetry(EventData):
    agent: Optional[str] = None
    schema_: str = Field(alias="schema")
    attempt: int
    errors: List[str] = Field(default_factory=list)  # validation messages only

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PlanCacheLookup(EventData):
    datasource_id: str
    hit: bool


class PolicyDecision(EventData):
    """A denial at an enforcement point (D16); never an allowed table."""

    datasource_id: str
    schema_: Optional[str] = Field(default=None, alias="schema")
    table: Optional[str] = None  # None for a datasource
    rule: str
    step: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class HookDecision(EventData):
    hook: str
    point: HookPoint
    decision: Literal["allow", "modify", "deny", "needs_approval", "approved", "error"]
    reason: Optional[str] = None
    sub_query_id: Optional[str] = None
    approver: Optional[str] = None  # for approved
    seconds: float = 0.0


class RunLimitReached(EventData):
    limit: Literal["max_cost_usd", "max_llm_calls"]
    value: float
    spent: float


class SanityFlagged(EventData):
    code: str
    sub_query_id: Optional[str] = None
    retry: bool = False


# ---------------------------------------------------------------- engine events

class DatasourceFailed(EventData):
    datasource_id: str
    stage: Optional[str] = None
    message: str
    fix: Optional[str] = None


class IndexStarted(EventData):
    datasource_id: str
    namespace: str


class IndexColumnFailed(EventData):
    datasource_id: str
    table: str
    column: Optional[str] = None
    stage: str
    message: str


class IndexSwapped(EventData):
    datasource_id: str
    namespace: str
    build_id: str
    schema_version: Optional[str] = None
    replaced_build_id: Optional[str] = None
    replaced_schema_version: Optional[str] = None
    counts: Dict[str, int] = Field(default_factory=dict)


class IndexFailed(EventData):
    datasource_id: str
    reason: str


class SchemaDrifted(EventData):
    datasource_id: str
    from_version: str
    to_version: Optional[str] = None  # None for the live database
    detected_by: Literal["check", "build"]
    counts: Dict[str, int] = Field(default_factory=dict)  # the diff's counts


class RetentionPurged(EventData):
    counts: Dict[str, int] = Field(default_factory=dict)  # per slot


class LLMChanged(EventData):
    by: Dict[str, Any] = Field(default_factory=dict)
    changes: Dict[str, Dict[str, Optional[str]]] = Field(default_factory=dict)  # never a key


class EvalCase(EventData):
    suite: str
    case_id: str
    role: str
    passed: Optional[bool] = Field(default=None, alias="pass")
    source: Optional[Literal["verified", "generated"]] = None
    scores: Dict[str, Any] = Field(default_factory=dict)
    cost: Optional[float] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvalFinished(EventData):
    suite: str
    cases: int
    accuracy: Optional[float] = None
    accuracy_generated: Optional[float] = None
    cost: Optional[float] = None
    gate: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- the catalog

class EventSpec(BaseModel):
    """One catalogued event: its data model, what it means, who publishes it, and whether a run has it."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    model: Type[EventData]
    description: str
    run: bool
    stream: bool = False  # on the caller's stream (D17)
    audit: bool = False   # written by the audit observer (D19)
    # With ``audit``: write the event only when each named data field has one of these values.
    audit_only: Dict[str, FrozenSet[str]] = Field(default_factory=dict)

    def audited(self, data: Dict[str, Any]) -> bool:
        """Whether the audit observer writes an event with this ``data``."""
        return self.audit and all(data.get(field) in values for field, values in self.audit_only.items())


class EventInfo(BaseModel):
    """What ``engine.events.catalog()`` returns per event: its name, description and data's JSON Schema."""

    name: str
    description: str
    run: bool
    stream: bool = False
    audit: bool = False
    audit_only: Dict[str, List[str]] = Field(default_factory=dict)
    data_schema: Dict[str, Any]


def _spec(model: Type[EventData], description: str, *, run: bool, stream: bool = False,
          audit: bool = False, audit_only: Optional[Dict[str, FrozenSet[str]]] = None) -> EventSpec:
    return EventSpec(model=model, description=description, run=run, stream=stream, audit=audit,
                     audit_only=audit_only or {})


CATALOG: Dict[str, EventSpec] = {
    # Run events
    "run.started": _spec(RunStarted, "A question began. Published by RunScope when it opens.", run=True,
                         stream=True),
    "run.finished": _spec(RunFinished, "A question ended with a result: its summary, never rows. Published "
                                       "once, last, by RunScope.", run=True, stream=True, audit=True),
    "run.failed": _spec(RunFailed, "A question could not start, or crashed outside the pipeline. Published "
                                   "once, last, by RunScope.", run=True, stream=True, audit=True),
    "node.started": _spec(NodeStarted, "A pipeline step began.", run=True, stream=True),
    "node.finished": _spec(NodeFinished, "A pipeline step ended; ok is False when it raised.", run=True,
                           stream=True),
    "answer.delta": _spec(AnswerDelta, "The next piece of the answer's summary or content, as the model "
                                       "writes it. Only a streamed run's answer model streams.", run=True,
                         stream=True),
    "result.manifest": _spec(ResultManifestData, "The run's results were saved: what they are, never their "
                                                 "rows.", run=True, stream=True),
    "llm.call": _spec(LLMCall, "A model call finished: tokens, latency and cost. Published by "
                                 "TokenUsageCallback.", run=True),
    "llm.parse_retry": _spec(LLMParseRetry, "A structured answer did not parse and is asked for again (E4).",
                             run=True),
    "plan_cache.lookup": _spec(PlanCacheLookup, "The plan cache was asked for a plan. Published by "
                                                 "PlanCache.get.", run=True),
    "policy.decision": _spec(PolicyDecision, "A denial at an enforcement point: the validator, the curated "
                                             "matcher or the resolver (E4).", run=True, audit=True),
    "hook.decision": _spec(HookDecision, "A hook answered at one of the six points (H1). The audit "
                                         "observer writes deny, needs_approval, approved and error.",
                           run=True, audit=True,
                           audit_only={"decision": frozenset({"deny", "needs_approval", "approved", "error"})}),
    "run.limit_reached": _spec(RunLimitReached, "The question reached its cost or call cap (E3).", run=True,
                               stream=True, audit=True),
    "sanity.flagged": _spec(SanityFlagged, "An answer sanity check found something (S1).", run=True,
                            stream=True),
    # Engine events
    "datasource.failed": _spec(DatasourceFailed, "A datasource check failed (E4).", run=False),
    "index.started": _spec(IndexStarted, "An index build began (E4).", run=False),
    "index.column_failed": _spec(IndexColumnFailed, "An index build could not index a column (E4).", run=False),
    "index.swapped": _spec(IndexSwapped, "An index build went live (E4).", run=False, audit=True),
    "index.failed": _spec(IndexFailed, "An index build failed (E4).", run=False),
    "schema.drifted": _spec(SchemaDrifted, "A datasource's structure differs from its build (D1).", run=False,
                            audit=True),
    "retention.purged": _spec(RetentionPurged, "Old traces, results and audit files were deleted (E4).",
                              run=False, audit=True),
    "llm.changed": _spec(LLMChanged, "The LLM configuration was replaced; never a key. Published by "
                                      "engine.llm.replace.", run=False,
                         audit=True),
    "eval.case": _spec(EvalCase, "An eval case finished (V1).", run=False),
    "eval.finished": _spec(EvalFinished, "An eval run finished (V1).", run=False),
}

RUN_EVENTS: FrozenSet[str] = frozenset(name for name, spec in CATALOG.items() if spec.run)
# The caller-safe allow-list (D17): what ``stream_query`` yields of its own run.
STREAM_EVENTS: FrozenSet[str] = frozenset(name for name, spec in CATALOG.items() if spec.stream)
# What the ``audit`` observer writes (D19); ``EventSpec.audited`` applies an entry's ``audit_only``.
AUDIT_EVENTS: FrozenSet[str] = frozenset(name for name, spec in CATALOG.items() if spec.audit)


def catalog() -> List[EventInfo]:
    """Every catalogued event: name, description, whether a run has it, whether the caller's stream
    carries it and the audit observer writes it, and its data's JSON Schema."""
    return [EventInfo(name=name, description=spec.description, run=spec.run, stream=spec.stream,
                      audit=spec.audit, audit_only={k: sorted(v) for k, v in spec.audit_only.items()},
                      data_schema=spec.model.model_json_schema(by_alias=True))
            for name, spec in CATALOG.items()]


def check_names(names: Any) -> FrozenSet[str]:
    """``names`` as a set, each one catalogued.

    Raises:
        ValueError: a name is not in the catalog.
    """
    found = frozenset(names)
    unknown = sorted(found - set(CATALOG))
    if unknown:
        raise ValueError(f"Not in the event catalog: {', '.join(unknown)}.")
    return found
