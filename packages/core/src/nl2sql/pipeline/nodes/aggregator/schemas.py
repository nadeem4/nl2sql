from typing import Literal
from pydantic import BaseModel, Field


class AggregatedResponse(BaseModel):
    """Structured response for the aggregator node.

    Attributes:
        summary (str): A concise summary of the aggregated results.
        format_type (Literal): The format to present the data (table, list, text).
        content (str): The aggregated content formatted according to format_type.
    """
    summary: str = Field(description="A concise summary of the aggregated results.")
    format_type: Literal["table", "list", "text"] = Field(
        description="The best format to present the data: 'table' for structured data, 'list' for items, 'text' for narrative."
    )
    content: str = Field(description="The aggregated content formatted according to format_type (e.g., Markdown table, bullet points, or paragraph).")
