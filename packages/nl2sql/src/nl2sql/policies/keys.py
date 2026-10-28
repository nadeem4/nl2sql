"""The API key token: its format, its hash and the constant-time check (docs/architecture/wave_c.md, section 9; D127).

A token is ``nl2sql_<id>_<secret>``: ``id`` is 8 hex characters (shown in lists and logs), ``secret`` is 32 random
bytes, URL-safe. Only ``sha256(secret)`` is stored. SHA-256 is enough because the secret is high-entropy: a
guess cannot be searched for, so a salt or a slow hash would add cost and no safety.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Optional, Tuple

PREFIX = "nl2sql"
_TOKEN = re.compile(rf"{PREFIX}_([0-9a-f]{{8}})_([A-Za-z0-9_-]{{43}})")  # 32 bytes, base64 without padding


def new_id() -> str:
    """A key id: 8 hex characters."""
    return secrets.token_hex(4)


def new_token(key_id: str) -> Tuple[str, str]:
    """``(token, hash)`` for ``key_id``: the token to hand out once, the hash to store."""
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}_{key_id}_{secret}", hash_secret(secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse(token: Any) -> Optional[Tuple[str, str]]:
    """``(id, secret)`` of a well-formed token, else None."""
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    return (match.group(1), match.group(2)) if match else None


def matches(secret: str, stored_hash: str) -> bool:
    """Whether ``secret`` hashes to ``stored_hash``, compared in constant time."""
    return hmac.compare_digest(hash_secret(secret), stored_hash)
