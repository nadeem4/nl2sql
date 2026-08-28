from pydantic import BaseModel
from typing import Optional

class RunConfig(BaseModel):
    """Configuration for running the pipeline."""
    query: str
    ds_id: Optional[str] = None
    role: str = "admin"
    no_exec: bool = False
    verbose: bool = False
    show_perf: bool = False


