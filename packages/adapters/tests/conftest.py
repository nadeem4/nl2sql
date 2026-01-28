import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADAPTERS_ROOT = ROOT / "packages" / "adapters"

paths = [
    ROOT / "packages" / "adapter-sdk" / "src",
    ROOT / "packages" / "adapter-sqlalchemy" / "src",
    ROOT / "packages" / "core" / "src",
    ADAPTERS_ROOT / "sqlite" / "src",
    ADAPTERS_ROOT / "postgres" / "src",
    ADAPTERS_ROOT / "mysql" / "src",
    ADAPTERS_ROOT / "mssql" / "src",
]

for path in paths:
    sys.path.insert(0, str(path))
