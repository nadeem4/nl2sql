"""Scorers: what makes an eval case pass (wave_a.md, section 8, "Scorers").

A scorer is a registry kind (``nl2sql.scorers``, interface version 1). The
engine ships five, declared as entry points in its own ``pyproject.toml``:
``result_match``, ``outcome``, ``retrieval_recall``, ``faithfulness`` and
``routing``. A plugin registers its own the same way, or in code with
``registry.add_scorer`` / ``@registry.scorer``, and names it in ``evals.yaml``'s
``scorers``.

Each scorer is given one :class:`CaseRun` (a case, the role it ran as and what
the run returned) and returns a :class:`Score`. ``value`` and ``passed`` are
``None`` when the scorer does not apply to the case (``result_match`` on a
refusal, ``faithfulness`` with no answer). A case passes when every scorer
that applies to it passes. A scorer that raises scores the case as an error;
the run goes on.

The engine side, not the SDK: a scorer sees engine types (``QueryResult``).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Protocol, Type, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.api.query_api import QueryResult
from nl2sql.evaluation.suite import EvalCase

Mode = str  # "full", "plans" or "retrieval"
MODES = ("full", "plans", "retrieval")


class Score(BaseModel):
    """One scorer's verdict on one case run."""

    value: Optional[float] = None  # 0..1; None when the scorer does not apply to this case
    passed: Optional[bool] = None
    detail: Dict[str, Any] = Field(default_factory=dict)

    @property
    def applies(self) -> bool:
        return self.passed is not None or "error" in self.detail


NOT_APPLICABLE = Score()


class CaseRun(BaseModel):
    """What a scorer is given: one case, run once as one role."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: EvalCase
    role: str
    mode: Mode
    result: QueryResult = Field(default_factory=QueryResult)
    # The first result set's rows, every row (not the capped sample a caller sees).
    rows: Optional[List[List[Any]]] = None
    columns: List[str] = Field(default_factory=list)
    # What the schema retriever sent the planner, table -> columns; None when it did not run.
    retrieval: Optional[Dict[str, List[str]]] = None
    # Mode retrieval: the datasources routing ranked for the question, best first.
    routed: Optional[List[str]] = None

    @property
    def expected(self) -> str:
        return self.case.outcome[self.role]


@runtime_checkable
class Scorer(Protocol):
    """The scorer interface, version 1."""

    interface_version: ClassVar[int]
    config_model: ClassVar[Type[BaseModel]]
    modes: ClassVar[FrozenSet[str]]  # the modes it can score

    @classmethod
    def from_config(cls, cfg: BaseModel) -> "Scorer": ...

    def score(self, run: CaseRun) -> Score: ...


class NoConfig(BaseModel):
    """A scorer with nothing to configure."""

    model_config = ConfigDict(extra="forbid")


class BuiltinScorer:
    """The built-ins' shared parts: interface version 1, no config."""

    interface_version: ClassVar[int] = 1
    config_model: ClassVar[Type[BaseModel]] = NoConfig
    modes: ClassVar[FrozenSet[str]] = frozenset()

    @classmethod
    def from_config(cls, cfg: BaseModel) -> "BuiltinScorer":
        return cls()

    def score(self, run: CaseRun) -> Score:  # pragma: no cover - each built-in overrides it
        raise NotImplementedError


def build_scorers(registry: Any, names: List[str]) -> Dict[str, Scorer]:
    """Each named scorer, loaded through ``registry`` and built from its default config.

    Raises:
        RegistryError: a name is not registered (with the closest name).
    """
    scorers: Dict[str, Scorer] = {}
    for name in names:
        cls = registry.load("scorer", name)
        scorers[name] = cls.from_config(cls.config_model())
    return scorers


def run_scorer(scorer: Scorer, run: CaseRun) -> Score:
    """``scorer.score(run)``; a scorer that raises scores the case as an error, with its message."""
    try:
        return scorer.score(run)
    except Exception as exc:  # one broken scorer must not abort the run
        return Score(detail={"error": f"{type(exc).__name__}: {exc}"})


__all__ = ["Score", "CaseRun", "Scorer", "BuiltinScorer", "NoConfig", "NOT_APPLICABLE", "MODES",
           "build_scorers", "run_scorer"]
