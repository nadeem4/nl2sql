"""Eval suites: the case format, its loader and its writer (wave_a.md, section 8, "The files").

A suite is a YAML file of cases (``version: 1``, ``cases: [...]``). It is
data, read when a run asks for it, never when the engine starts. Each case
is a question with what a role should get: ``outcome`` maps every role to
``allowed``, ``refused``, ``unanswerable`` or ``clarify``. An answerable case
carries the SQL a person reviewed (``sql``, and reviewed alternatives in
``alt_sql``) and the rows it returns (``rows``, ``alt_rows``), which only
``regenerate`` writes, and optionally a hand-written plan (``plan``, a
``PlanModel`` as data) for mode ``plans``.

Only ``status: approved`` cases run. A seeded or exported case is a
``draft`` until a person reviews it.

The suites the package ships are in ``suites/``: ``chinook.yaml`` is the
Chinook gold set, moved here unchanged from ``chinook_gold.yaml`` and
``chinook_gold_plans.yaml``; ``routing.yaml`` asks which of the demo's three
databases a question belongs to (Phase R).
"""
from __future__ import annotations

import hashlib
import pathlib
from typing import Any, Dict, List, Literal, Optional, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SUITES_DIR = pathlib.Path(__file__).parent / "suites"
CHINOOK_SUITE_PATH = SUITES_DIR / "chinook.yaml"
ROUTING_SUITE_PATH = SUITES_DIR / "routing.yaml"

# What a role should get. ``clarify``: the engine should ask the caller to
# narrow the question (Wave B); evals run such a case only with ``clarify=True``.
Outcome = Literal["allowed", "refused", "unanswerable", "clarify"]
Difficulty = Literal["easy", "medium", "hard"]
CaseStatus = Literal["draft", "approved", "retired"]
Origin = Literal["hand", "seeded", "feedback"]
# ``answer``: the case checks what the question returns. ``routing``: only where
# it goes (its ``datasources``); it has no ``sql`` and no execution verdict.
CaseKind = Literal["answer", "routing"]

Rows = List[Dict[str, Any]]


class EvalCase(BaseModel):
    """One question, what each role should get, and the reviewed answer."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    datasources: List[str] = Field(default_factory=list)
    status: CaseStatus = "draft"
    kind: CaseKind = "answer"
    origin: Origin = "hand"
    difficulty: Optional[Difficulty] = None
    tags: List[str] = Field(default_factory=list)
    paraphrase_group: Optional[str] = None
    outcome: Dict[str, Outcome]
    tables: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    order_matters: bool = False
    sql: Optional[str] = None
    rows: Optional[Rows] = None
    # Other answers a reviewer read the rows of and accepted: a full name in
    # one column, a year labelled by its first day. Each is run by
    # ``regenerate`` exactly as ``sql`` is; their rows are never typed by hand.
    alt_sql: List[str] = Field(default_factory=list)
    alt_rows: List[Rows] = Field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None  # a PlanModel, as data, for mode plans

    @model_validator(mode="after")
    def _alternatives_are_answerable(self) -> "EvalCase":
        if self.kind == "routing" and (self.sql is not None or self.alt_sql):
            raise ValueError(f"{self.id}: a routing case says where a question goes and has no sql; "
                             "give the case kind: answer to check its rows")
        if (self.kind == "answer" and self.status == "approved" and self.sql is None
                and "allowed" in self.outcome.values()):
            raise ValueError(f"{self.id}: an approved case a role is allowed to answer has no sql. Add the "
                             "reviewed sql, or mark it kind: routing if it only says where the question goes")
        if self.alt_sql and self.sql is None:
            raise ValueError(f"{self.id}: an unanswerable case cannot have an alternative answer")
        if self.status == "approved" and len(self.alt_rows) != len(self.alt_sql):
            raise ValueError(f"{self.id}: {len(self.alt_sql)} alt_sql but {len(self.alt_rows)} alt_rows; "
                             "run engine.evals.regenerate on the suite")
        return self

    def answers(self) -> List[Rows]:
        """The reviewed answer and every alternative, ``rows`` first."""
        return [self.rows or [], *self.alt_rows]


class EvalSuite(BaseModel):
    """A suite file: its cases, and where it was read from."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    cases: List[EvalCase] = Field(default_factory=list)
    path: Optional[pathlib.Path] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "EvalSuite":
        seen, repeated = set(), []
        for case in self.cases:
            if case.id in seen:
                repeated.append(case.id)
            seen.add(case.id)
        if repeated:
            raise ValueError(f"case ids repeat: {', '.join(sorted(set(repeated)))}")
        return self

    @property
    def name(self) -> str:
        return self.path.stem if self.path else "suite"

    def digest(self) -> str:
        """A SHA-256 of the file's LF bytes: two runs compare only on the same digest."""
        if self.path is None:
            return hashlib.sha256(dump_suite(self.model_dump(mode="json")).encode("utf-8")).hexdigest()
        return hashlib.sha256(self.path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    def approved(self) -> List[EvalCase]:
        return [case for case in self.cases if case.status == "approved"]


def load_suite(path: pathlib.Path) -> EvalSuite:
    """Read and validate one suite file."""
    path = pathlib.Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a suite is a mapping with 'version' and 'cases'")
    return EvalSuite.model_validate({**raw, "path": path})


def holds_approved_cases(path: pathlib.Path) -> bool:
    """Whether ``path`` exists and lists at least one ``status: approved`` case (read as plain YAML)."""
    path = pathlib.Path(path)
    if not path.is_file():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError):
        return False
    cases = raw.get("cases") if isinstance(raw, dict) else None
    return any(isinstance(c, dict) and c.get("status") == "approved" for c in cases or [])


def check_target(path: pathlib.Path, configured: Sequence[pathlib.Path]) -> None:
    """Refuse a file machine-written drafts (seeded or exported) must never go into.

    Raises:
        ValueError: ``path`` is a suite the package ships, one of ``configured``
            (the suites ``evals.yaml`` names, resolved), or a file holding
            approved cases.
    """
    target = pathlib.Path(path).resolve()
    if target.parent == SUITES_DIR.resolve():
        raise ValueError(f"{path} is a suite the package ships; drafts never go into it. Write them to another file.")
    if target in {pathlib.Path(p).resolve() for p in configured}:
        raise ValueError(f"{path} is a suite evals.yaml names; drafts never go into it. Write them to another file.")
    if holds_approved_cases(path):
        raise ValueError(f"{path} holds approved cases; drafts never go into it. Write them to another file.")


def select_cases(cases: Sequence[EvalCase], selectors: Sequence[str]) -> List[EvalCase]:
    """The cases whose id or one of whose tags is in ``selectors``, in suite order.

    Raises:
        ValueError: a selector matches no case id or tag.
    """
    known = {c.id for c in cases} | {t for c in cases for t in c.tags}
    unknown = [s for s in selectors if s not in known]
    if unknown:
        raise ValueError(f"No case id or tag matches: {', '.join(unknown)}")
    wanted = set(selectors)
    return [c for c in cases if c.id in wanted or wanted & set(c.tags)]


# ---------------------------------------------------------------- writing

class _Dumper(yaml.SafeDumper):
    """Block strings for multi-line SQL, and no anchors: each case reads on its own."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)

# Written only when they say something.
_OPTIONAL = {"paraphrase_group": None, "difficulty": None, "plan": None, "kind": "answer"}


def case_document(case: EvalCase) -> Dict[str, Any]:
    """A case as it is written to a suite file: empty optional keys left out."""
    item = case.model_dump(mode="json")
    for key, empty in _OPTIONAL.items():
        if item[key] == empty:
            del item[key]
    if not item["alt_sql"]:
        del item["alt_sql"]
        del item["alt_rows"]
    return item


def dump_suite(document: Dict[str, Any], header: str = "") -> str:
    """The suite file's text: ``header`` (comment lines), then the version and the cases, a blank line apart."""
    cases = [case_document(EvalCase.model_validate(c)) for c in document.get("cases") or []]
    body = yaml.dump({"version": document.get("version", 1), "cases": cases}, Dumper=_Dumper, sort_keys=False,
                     allow_unicode=True, width=120, default_flow_style=None)
    return header + body.replace("\n- id:", "\n\n- id:")


def file_header(path: pathlib.Path) -> str:
    """The comment lines a suite file starts with, kept when the file is rewritten."""
    lines = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines(keepends=True):
        if not line.startswith("#") and line.strip():
            break
        lines.append(line)
    return "".join(lines)


def write_suite(path: pathlib.Path, document: Dict[str, Any], header: str = "") -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_suite(document, header), encoding="utf-8", newline="\n")
