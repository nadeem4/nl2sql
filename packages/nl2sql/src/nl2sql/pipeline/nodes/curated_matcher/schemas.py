from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

# The id the model picks when no candidate answers the whole question.
NONE_OF_THESE = "none_of_these"


class ParamValue(BaseModel):
    """One parameter value, as text. Code parses and checks it against the query's declared type."""

    name: str = Field(description="The parameter's name, as listed for the chosen query.")
    value: str = Field(description="Its value, taken from the question, as text.")


class VerifiedQueryChoice(BaseModel):
    """Pick the verified query that answers the question, or none_of_these.

    Call this tool with query_id set to one candidate's id (datasource.query)
    and a value for each of its parameters the question gives, or with
    query_id "none_of_these" when no candidate answers the whole question as
    asked.
    """

    query_id: str = Field(description='A candidate id, exactly as listed, or "none_of_these".')
    params: List[ParamValue] = Field(default_factory=list,
                                     description="A value for each parameter the question gives.")
    reason: str = Field(default="", description="One short sentence on why.")


class VerifiedMatch(BaseModel):
    """The verified query that answered the run: what provenance and the answer writer read."""

    datasource_id: str
    query_id: str
    description: str
    params: Dict[str, Any] = Field(default_factory=dict)
