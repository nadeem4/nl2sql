"""Hosted mode's default limits, per visitor.

Here rather than in ``playground.hosted`` so the CLI can show them in
``nl2sql demo --help`` without importing the playground's web stack.
``nl2sql demo --questions-per-minute N --questions-per-session N`` overrides
them.
"""

DEFAULT_QUESTIONS_PER_MINUTE = 6
DEFAULT_QUESTIONS_PER_SESSION = 30
