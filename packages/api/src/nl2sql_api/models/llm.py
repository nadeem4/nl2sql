from pydantic import BaseModel
from typing import Dict, Any, Optional

class LLMRequest(BaseModel):
    config: Dict[str, Any]


class LLMResponse(BaseModel):
    success: bool
    message: str
    llm_name: Optional[str] = None