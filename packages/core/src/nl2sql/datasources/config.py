from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class FeatureFlags:
    """
    Configuration flags for database capabilities and safety features.

    Attributes:
        allow_generate_writes: If True, allows generation of DML/DDL (DANGEROUS).
        allow_cross_db: If True, allows cross-database queries (if supported).
        supports_dry_run: If True, engine supports dry-run validation.
        supports_estimated_cost: If True, engine supports cost estimation.
        sample_rows_enabled: If True, allows fetching sample rows.
    """
    allow_generate_writes: bool = False
    allow_cross_db: bool = False
    supports_dry_run: bool = False
    supports_estimated_cost: bool = False
    sample_rows_enabled: bool = True


@dataclasses.dataclass
class DatasourceProfile:
    """
    Configuration profile for a data source.

    Attributes:
        id: Unique identifier for the profile.
        engine: Database engine type (e.g., "sqlite", "postgres"). Optional if implied by URL.
        sqlalchemy_url: SQLAlchemy connection string.
        auth: Optional authentication details.
        read_only_role: Optional role to assume for read-only access.
        statement_timeout_ms: Timeout for queries in milliseconds.
        row_limit: Maximum number of rows to return.
        max_bytes: Maximum response size in bytes.
        tags: Metadata tags.
        feature_flags: Capability flags.
    """
    id: str
    sqlalchemy_url: str
    engine: Optional[str] = None
    auth: Optional[Dict[str, Any]] = None
    read_only_role: Optional[str] = None
    description: Optional[str] = None
    statement_timeout_ms: int = 8000
    row_limit: int = 1000
    max_bytes: int = 10 * 1024 * 1024
    tags: Dict[str, Any] = dataclasses.field(default_factory=dict)
    feature_flags: FeatureFlags = dataclasses.field(default_factory=FeatureFlags)
    date_format: str = "ISO 8601"


def _to_feature_flags(raw: Optional[Dict[str, Any]]) -> FeatureFlags:
    """Parses feature flags from a dictionary."""
    if not raw:
        return FeatureFlags()
    return FeatureFlags(
        allow_generate_writes=bool(raw.get("allow_generate_writes", False)),
        allow_cross_db=bool(raw.get("allow_cross_db", False)),
        supports_dry_run=bool(raw.get("supports_dry_run", False)),
        supports_estimated_cost=bool(raw.get("supports_estimated_cost", False)),
        sample_rows_enabled=bool(raw.get("sample_rows_enabled", True)),
    )


def _normalize_engine_id(backend: str) -> str:
    """Normalizes SQLAlchemy backend names to internal Adapter IDs."""
    backend = backend.lower()
    if backend == "postgresql": return "postgres"
    if backend == "mssql": return "mssql"
    if backend == "sqlserver": return "mssql"
    if backend == "mysql": return "mysql"
    if backend == "sqlite": return "sqlite"
    if backend == "oracle": return "oracle"
    return backend

def _infer_engine(url: str) -> str:
    """Infers the engine type from the SQLAlchemy URL."""
    try:
        from sqlalchemy.engine import make_url
        u = make_url(url)
        return _normalize_engine_id(u.get_backend_name())
    except Exception:
        return "unknown"


def _to_profile(raw: Dict[str, Any]) -> DatasourceProfile:
    """Parses a datasource profile from a dictionary."""
    url = raw["sqlalchemy_url"]
    engine = raw.get("engine")
    
    if not engine:
        engine = _infer_engine(url)
    else:
        # Normalize explicit engine too
        engine = _normalize_engine_id(engine)

    return DatasourceProfile(
        id=raw["id"],
        description=raw.get("description"),
        engine=engine,
        sqlalchemy_url=url,
        auth=raw.get("auth"),
        read_only_role=raw.get("read_only_role"),
        statement_timeout_ms=int(raw.get("statement_timeout_ms", 8000)),
        row_limit=int(raw.get("row_limit", 1000)),
        max_bytes=int(raw.get("max_bytes", 10 * 1024 * 1024)),
        tags=raw.get("tags", {}) or {},
        feature_flags=_to_feature_flags(raw.get("feature_flags") or {}),
        date_format=raw.get("date_format", "ISO 8601"),
    )


def load_profiles(path: pathlib.Path) -> Dict[str, DatasourceProfile]:
    """
    Load datasource profiles from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A dictionary mapping profile IDs to DatasourceProfile objects.

    Raises:
        RuntimeError: If PyYAML is not installed.
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config format is invalid.
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
    """
    Retrieves a profile by ID.

    Args:
        profiles: Dictionary of available profiles.
        profile_id: ID of the profile to retrieve.

    Returns:
        The requested DatasourceProfile.

    Raises:
        KeyError: If the profile ID is not found.
    """
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise KeyError(f"Datasource profile '{profile_id}' not found") from exc
