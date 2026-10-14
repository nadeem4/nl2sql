"""Who may read what: ``UserContext``, ``PolicyEngine`` and the ``Scope`` every access decision asks.

Nothing outside this package matches a policy pattern.
"""
from .models import UserContext
from .patterns import matching_datasources
from .scope import PolicyEngine, Scope

__all__ = ["UserContext", "PolicyEngine", "Scope", "matching_datasources"]
