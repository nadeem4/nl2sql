from pydantic import BaseModel, Field
from typing import Literal, Optional

class IntentValidationResult(BaseModel):
    """Result of the intent validation check."""
    
    is_safe: bool = Field(description="Whether the query is safe to proceed.")
    violation_category: Optional[Literal["jailbreak", "pii_exfiltration", "destructive", "system_probing", "none"]] = Field(
        default="none",
        description="Category of the violation if unsafe."
    )
    reasoning: str = Field(description="Explanation of why the query is safe or unsafe.")
