"""Re-export of the canonical ``AgentConfig``.

The model lives in :mod:`nl2sql.configs.llm`, which owns the on-disk file
schemas. This module keeps the ``nl2sql.llm.models`` import path working for
``LLMRegistry`` and existing callers.
"""

from nl2sql.configs.llm import AgentConfig

__all__ = ["AgentConfig"]
