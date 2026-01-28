import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQLA_SRC = ROOT / "packages" / "adapter-sqlalchemy" / "src"
SDK_SRC = ROOT / "packages" / "adapter-sdk" / "src"

for path in (SQLA_SRC, SDK_SRC):
    sys.path.insert(0, str(path))
