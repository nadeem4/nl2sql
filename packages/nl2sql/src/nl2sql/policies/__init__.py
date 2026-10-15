"""Who may read what: ``UserContext``, ``PolicyEngine`` and the ``Scope`` every access decision asks.

Nothing outside this package matches a policy pattern.
"""
from nl2sql_sdk.policy import UserContext
from .patterns import matching_datasources, patterns_matching
from .scope import PolicyEngine, Scope

__all__ = ["UserContext", "PolicyEngine", "Scope", "matching_datasources", "patterns_matching"]
