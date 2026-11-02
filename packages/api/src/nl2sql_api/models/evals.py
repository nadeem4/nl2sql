from pydantic import BaseModel


class SuiteInfo(BaseModel):
    """One eval suite named in ``evals.suites``: ``path`` is as configured, and ``approved`` counts the cases that run."""

    name: str
    path: str
    cases: int
    approved: int
