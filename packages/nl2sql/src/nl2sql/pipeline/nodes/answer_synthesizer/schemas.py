from typing import Literal, List, Dict, Any, Optional

from pydantic import BaseModel, Field


class AggregatedResponse(BaseModel):
    """Structured response for the answer synthesizer."""
    summary: str = Field(description="A concise summary of the aggregated results.")
    format_type: Literal["table", "list", "text"] = Field(
        description=(
            "How content is written: 'text' for a paragraph, 'list' for a few items, 'table' "
            "only for a few preview rows. The full result is shown to the user separately."
        )
    )
    content: str = Field(
        description=(
            "The answer, written according to format_type, using only the preview rows and "
            "the row count. Do not copy the whole result: it is shown as a table."
        )
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Optional warnings to communicate missing or skipped results.",
    )


class AnswerSynthesizerResponse(BaseModel):
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Any] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
