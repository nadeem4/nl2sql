"""
Result API for NL2SQL

Provides functionality for result management and storage.
"""

from __future__ import annotations

from typing import Optional

from nl2sql.context import NL2SQLContext
from nl2sql.execution.contracts import ArtifactRef
from nl2sql_adapter_sdk.contracts import ResultFrame


class ResultAPI:
    """
    API for result management and storage.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
    
    