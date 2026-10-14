"""The body of the ``curated`` section: verified queries and metrics, per datasource.

A verified query is SQL a person wrote, in the dialect of its datasource, with
typed ``:name`` parameters and the questions it answers. These models check
what needs no database: shape, unique ids, question placeholders against
declared params, and values against declared types. The checks that need the
datasources (the SQL parses in the datasource's dialect and is one read-only
query) live with ``check_curated`` in ``nl2sql.configs.curated``.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

ParamValue = Union[StrictInt, StrictFloat, StrictStr]

_SLUG = r"^[a-z][a-z0-9_]*$"
_QUESTION_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _typed(kind: str, value: ParamValue) -> Union[int, float, str, date]:
    """``value`` as the declared ``kind``, or ``ValueError``."""
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ValueError(f"{value!r} is not an int")
    if kind == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(f"{value!r} is not a number")
    if kind == "date":
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)")
    if isinstance(value, str):
        return value
    raise ValueError(f"{value!r} is not a string")


class ParamSpec(BaseModel):
    """One typed parameter of a verified query."""

    type: Literal["int", "float", "string", "date"]
    default: Optional[ParamValue] = None
    min: Optional[ParamValue] = None
    max: Optional[ParamValue] = None
    choices: Optional[List[StrictStr]] = None
    example: Optional[ParamValue] = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _values_agree_with_the_type(self) -> "ParamSpec":
        if self.type == "string" and (self.min is not None or self.max is not None):
            raise ValueError("min/max apply to int, float and date params, not string")
        if self.choices is not None and self.type != "string":
            raise ValueError("choices apply to string params only")

        bounds = {}
        for name in ("min", "max"):
            value = getattr(self, name)
            if value is not None:
                try:
                    bounds[name] = _typed(self.type, value)
                except ValueError as exc:
                    raise ValueError(f"{name}: {exc}") from None
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise ValueError(f"min {self.min!r} is greater than max {self.max!r}")

        for name in ("default", "example"):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                typed = _typed(self.type, value)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
            if "min" in bounds and typed < bounds["min"]:
                raise ValueError(f"{name} {value!r} is below min {self.min!r}")
            if "max" in bounds and typed > bounds["max"]:
                raise ValueError(f"{name} {value!r} is above max {self.max!r}")
            if self.choices is not None and value not in self.choices:
                raise ValueError(f"{name} {value!r} is not one of choices {self.choices}")
        return self


class VerifiedQuery(BaseModel):
    """A query a person wrote and reviewed, and the questions it answers."""

    id: str = Field(pattern=_SLUG)
    description: str
    questions: List[str] = Field(min_length=1)
    params: Dict[str, ParamSpec] = Field(default_factory=dict)
    sql: str

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _placeholders_match_params(self) -> "VerifiedQuery":
        used = {name for q in self.questions for name in _QUESTION_PLACEHOLDER.findall(q)}
        undeclared = sorted(used - set(self.params))
        if undeclared:
            raise ValueError(f"query '{self.id}': question placeholders {undeclared} are not declared params")
        unused = sorted(set(self.params) - used)
        if unused:
            raise ValueError(f"query '{self.id}': params {unused} appear in no question")
        return self


class MetricDefinition(BaseModel):
    """A named business expression. Parsed so the format is fixed; not used yet."""

    name: str
    description: str = ""
    expression: str
    table: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class CuratedDatasource(BaseModel):
    """The curated answers of one datasource."""

    verified_queries: List[VerifiedQuery] = Field(default_factory=list)
    metrics: List[MetricDefinition] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("verified_queries")
    @classmethod
    def _unique_ids(cls, queries: List[VerifiedQuery]) -> List[VerifiedQuery]:
        ids = [q.id for q in queries]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate verified query ids: {duplicates}")
        return queries
