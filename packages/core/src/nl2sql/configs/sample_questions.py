from typing import Dict, List

from pydantic import BaseModel, Field, ConfigDict


class SampleQuestionsFileConfig(BaseModel):
    """File-level schema for sample_questions.yaml."""

    datasources: Dict[str, List[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")
