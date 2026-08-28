from typing import Literal, List, Dict, Any, Optional

from pydantic import BaseModel, Field


class AggregatedResponse(BaseModel):
    """Structured response for the answer synthesizer."""
    summary: str = Field(description="A concise summary of the aggregated results.")
    format_type: Literal["table", "list", "text"] = Field(
        description="The best format to present the data: 'table' for structured data, 'list' for items, 'text' for narrative."
    )
    content: str = Field(
        description="The aggregated content formatted according to format_type (e.g., Markdown table, bullet points, or paragraph)."
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Optional warnings to communicate missing or skipped results.",
    )


class AnswerSynthesizerResponse(BaseModel):
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Any] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
