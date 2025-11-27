from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class FeatureFlags:
    allow_generate_writes: bool = False
    allow_cross_db: bool = False
    supports_dry_run: bool = False
    supports_estimated_cost: bool = False
    sample_rows_enabled: bool = True


@dataclasses.dataclass
class DatasourceProfile:
    id: str
    engine: str
    sqlalchemy_url: str
    auth: Optional[Dict[str, Any]]
    read_only_role: Optional[str]
    statement_timeout_ms: int = 8000
    row_limit: int = 1000
    max_bytes: int = 10 * 1024 * 1024
    tags: Dict[str, Any] = dataclasses.field(default_factory=dict)
    feature_flags: FeatureFlags = dataclasses.field(default_factory=FeatureFlags)


def _to_feature_flags(raw: Optional[Dict[str, Any]]) -> FeatureFlags:
    if not raw:
        return FeatureFlags()
    return FeatureFlags(
        allow_generate_writes=bool(raw.get("allow_generate_writes", False)),
        allow_cross_db=bool(raw.get("allow_cross_db", False)),
        supports_dry_run=bool(raw.get("supports_dry_run", False)),
        supports_estimated_cost=bool(raw.get("supports_estimated_cost", False)),
        sample_rows_enabled=bool(raw.get("sample_rows_enabled", True)),
    )


def _to_profile(raw: Dict[str, Any]) -> DatasourceProfile:
    return DatasourceProfile(
        id=raw["id"],
        engine=raw["engine"],
        sqlalchemy_url=raw["sqlalchemy_url"],
        auth=raw.get("auth"),
        read_only_role=raw.get("read_only_role"),
        statement_timeout_ms=int(raw.get("statement_timeout_ms", 8000)),
        row_limit=int(raw.get("row_limit", 1000)),
        max_bytes=int(raw.get("max_bytes", 10 * 1024 * 1024)),
        tags=raw.get("tags", {}) or {},
        feature_flags=_to_feature_flags(raw.get("feature_flags") or {}),
    )


def load_profiles(path: pathlib.Path) -> Dict[str, DatasourceProfile]:
    """
    Load datasource profiles from a YAML file. Returns a dict keyed by profile id.
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load datasource profiles") from exc

    if not path.exists():
        raise FileNotFoundError(f"Datasource config not found: {path}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("Datasource config must be a YAML list of profiles")

    profiles: Dict[str, DatasourceProfile] = {}
    for item in raw:
        profile = _to_profile(item)
        profiles[profile.id] = profile
    return profiles


def get_profile(profiles: Dict[str, DatasourceProfile], profile_id: str) -> DatasourceProfile:
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise KeyError(f"Datasource profile '{profile_id}' not found") from exc
